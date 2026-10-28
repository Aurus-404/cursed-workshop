import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.test_gun_assembly import Resources
from tests.test_geometry import eply
from workshop.compiler import compile_assembly
from workshop.geometry import parse_model, point
from workshop.support_vehicle import configuration, preview, catalog, public_catalog, SupportError
from workshop.syntax import parse, block
from workshop.resources import ResourceIndex


def resources():
    r = Resources()
    r.files['properties/resupply.inc'] = b'(define "supply_ammo" {extender "supply_zone" {limit %supply}{radius 10}{resupply {item "cartridge" "ammo" 10}}})'
    r.files['entity/crate/crate.def'] = b'{game_entity {model "crate"}}'
    r.files['entity/crate/crate.mdl'] = b'{skeleton {bone "crate" {VolumeView "crate.ply"}}}{volume "box" {bone "crate"}{box 10 10 10}}'
    r.files['entity/crate/crate.ply'] = eply()
    r._support_catalog = {'profiles': [{'id': 'native', 'kind': 'ammo', 'label': 'Native',
        'template': 'receiver', 'source': 'fixture', 'definition_path': 'entity/receiver/receiver.def',
        'capacity': 250, 'radius': 10, 'macro': 'supply_ammo',
        'body': '{extender "supply_zone" {limit 250}{radius 10}{resupply {item "cartridge" "ammo" 10}}}'}],
        'cargo': [{'id': 'crate', 'template': 'crate', 'label': 'Crate', 'kind': 'ammo'}]}
    return r


def spec():
    return {'schema': 1, 'profile': 'native', 'remove_mount': 'azimuth',
            'cargo': [{'asset': 'crate', 'position': [0, 0, 10], 'rotation': [0, 0, 0]}]}


class SupportVehicleTests(unittest.TestCase):
    def test_separate_free_mount_saves_restore_support_weapon_capabilities(self):
        # A native gun and coax share a joint/operator; two saved mounts do not.
        fixture = resources()
        fixture.files['properties/tank_crew.ext'] = b'{placer {place "gunner2"}{place "charger2"}}'
        views = []
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            game = target/'game'
            for name, raw in fixture.files.items():
                path = game/'resource'/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            index = ResourceIndex(game).build()
            index._support_catalog = fixture._support_catalog
            try:
                result = compile_assembly(index, 'receiver', 'receiver', {'support_vehicle': spec()}, target/'support', 'mw_support')
                current = index.with_overlay(target/'support'); views.append(current)
                original = parse(fixture.files['entity/receiver/receiver.def']).find('game_entity')
                for step in range(2):
                    output = target/str(step)
                    mount = '@free' if step == 0 else '@free:'+result['metadata']['bone_mapping']['elevation']
                    result = compile_assembly(current, result['id'], 'donor',
                        {'recipient_mount': mount, 'donor_mount': 'azimuth'}, output, 'mw_mount'+str(step))
                    root = parse((output/'resource'/result['definition_path']).read_bytes()).find('game_entity')
                    props = [a for node in root.find_all('props') for a in node.args]
                    self.assertIn('turret', props)
                    self.assertNotIn('-turret', props)
                    self.assertEqual('multi_turret' in props, step == 1)
                    self.assertEqual(root.find('mass').args, original.find('mass').args)
                    self.assertEqual(root.find('Chassis').render(), original.find('Chassis').render())
                    self.assertIsNotNone(root.find('extender'))
                    current = current.with_overlay(output); views.append(current)
            finally:
                for view in reversed(views): view.close()
                index.close()

    def compile(self, r=None, value=None):
        r = r or resources()
        with tempfile.TemporaryDirectory() as tmp:
            result = compile_assembly(r, 'receiver', 'receiver', {'support_vehicle': value or spec()}, tmp, 'mw_support')
            folder = Path(tmp)/'resource/entity/-vehicle/workshop/mw_support'
            root = parse((folder/'mw_support.def').read_bytes()).find('game_entity')
            model = parse_model((folder/'mw_support.mdl').read_bytes())
            recipe = json.loads((Path(tmp)/'assembly_mw_support.json').read_text(encoding='utf-8'))
        return result, root, model, recipe

    def test_removes_turret_and_operators_preserves_mobility_and_owns_no_extra_actor(self):
        result, root, model, recipe = self.compile()
        self.assertNotIn('azimuth', model['bones'])
        self.assertNotIn('turret_volume', model['volumes'])
        self.assertIn('body', model['bones'])
        self.assertEqual(root.find('mass').args, ['19000'])
        self.assertEqual(root.find('Chassis').find('speed').args, ['30'])
        self.assertEqual(result['metadata']['crew_roles'], ['driver'])
        self.assertEqual(len([n for n in model['bones'] if n.startswith('mw_cargo_')]), 2)
        self.assertEqual(set(model['volumes']), set())  # fixture hull has no volume; cargo is visual
        zones = [n for n in root.find_all('extender') if n.args == ['supply_zone']]
        self.assertEqual(zones[0].find('limit').args, ['250'])
        self.assertFalse(result['runtime_verified'])
        self.assertEqual(recipe['metadata']['transform']['support_vehicle'], spec())

    def test_zone_circle_sprite_travels_with_the_vehicle(self):
        # Native trucks draw the resupply radius with {DecalView supply_zone
        # {Sprite "supply_zone.ebm"}}, a file in their own folder.
        r = resources()
        ebm = b'{bitmap {Axis 1024 1024}{Blend Light}{Frame "$/service/supply_zone_ammo.dds"}}'
        r.files['entity/receiver/supply_zone.ebm'] = ebm
        profile = r._support_catalog['profiles'][0]
        profile['body'] += '{DecalView supply_zone {Sprite "supply_zone.ebm" {Visibility 0.25}}{Layer grounddynamic}{Visible 0}}'
        with tempfile.TemporaryDirectory() as tmp:
            compile_assembly(r, 'receiver', 'receiver', {'support_vehicle': spec()}, tmp, 'mw_support')
            folder = Path(tmp)/'resource/entity/-vehicle/workshop/mw_support'
            root = parse((folder/'mw_support.def').read_bytes()).find('game_entity')
            decal = [n for n in root.find_all('DecalView') if n.args == ['supply_zone']]
            self.assertEqual(len(decal), 1)
            sprite = decal[0].find('Sprite').args[0]
            self.assertFalse(sprite.startswith('/'))
            self.assertEqual((folder/sprite).read_bytes(), ebm)

    def test_preserves_hull_weapon_and_its_operator(self):
        r = resources()
        key = 'entity/receiver/receiver.def'
        doc = parse(r.files[key]);root = doc.find('game_entity')
        root.find('Weaponry').append(block('{place "hull_mg" {LinkBone "body"}{type "hull"}{weapon "mg" filling "mg_round belt" 50}{gunner "driver"}{charger "driver"}}'))
        r.files[key] = doc.render().encode()
        _, root, _, _ = self.compile(r)
        self.assertEqual([p.args[0] for p in root.find('Weaponry').find_all('place')], ['hull_mg'])

    def test_closed_turret_crew_is_retired_but_internal_hull_operator_survives(self):
        r = resources(); key = 'entity/receiver/receiver.def'
        doc = parse(r.files[key]); root = doc.find('game_entity')
        root.append(block('{placer {place "commander" {group "crew"}}'
                          '{place "gunner3" {group "crew"}{LinkBone ""}}'
                          '{place "passenger" {group "passenger"}{LinkBone "body"}}}'))
        root.find('Weaponry').append(block('{place "hull_mg" {LinkBone "body"}{type "hull"}'
            '{weapon "mg" filling "mg_round belt" 50}{gunner "gunner3"}{charger "gunner3"}}'))
        root.find('Weaponry').append(block('{place "flamer1" {type "ussr_smoke_barrel"}}'))
        r.files[key] = doc.render().encode()
        result, root, _, _ = self.compile(r)
        self.assertEqual(result['metadata']['crew_roles'], ['driver', 'gunner3'])
        self.assertIn('commander', result['metadata']['support_vehicle']['removed_roles'])
        places = {p.args[0] for pl in root.find_all('placer') for p in pl.find_all('place')}
        self.assertIn('passenger', places)
        self.assertNotIn('commander', places)

    def test_local_visual_wrapper_does_not_restore_retired_operator(self):
        r = resources(); key = 'entity/receiver/receiver.def'
        doc = parse(r.files[key]); root = doc.find('game_entity')
        root.append(block('(define "visual_wrapper" {placer {place "gunner" {LinkBone "coax"}}})'))
        root.append(block('("visual_wrapper")'))
        r.files[key] = doc.render().encode()
        result, root, _, _ = self.compile(r)
        self.assertNotIn('gunner', result['metadata']['crew_roles'])
        self.assertFalse(root.find_all('visual_wrapper'))

    def test_cargo_count_does_not_multiply_service_and_fittings_move_independently(self):
        s = spec();s['cargo'].append({'asset':'crate','position':[20, 10, 15], 'rotation':[0,0,90]})
        result, root, model, _ = self.compile(value=s)
        zones = [n for n in root.find_all('extender') if n.args == ['supply_zone']]
        self.assertEqual(len(zones), 1)
        self.assertEqual(zones[0].find('limit').args, ['250'])
        a = point(model['bones']['mw_cargo_0']['world'], [0,0,0])
        b = point(model['bones']['mw_cargo_1']['world'], [0,0,0])
        self.assertNotEqual(a,b)
        self.assertEqual(result['metadata']['support_vehicle']['profile']['capacity'],250)

    def test_preview_has_same_removed_parts_and_cargo_count(self):
        data = preview(resources(),'receiver',spec())
        self.assertTrue(data['preview_valid'])
        self.assertFalse(data['runtime_verified'])
        self.assertEqual(len(data['cargo']['meshes']),1)
        self.assertTrue(all(m['bone'] not in data['removed']['removed_bones'] for m in data['hull']['meshes']))

    def test_cargo_recipe_regenerates_deterministically(self):
        a, _, _, recipe = self.compile()
        b, _, _, _ = self.compile(value=recipe['metadata']['transform']['support_vehicle'])
        self.assertEqual(a['metadata']['file_hashes'],b['metadata']['file_hashes'])

    def test_rejects_unknown_profiles_assets_nonfinite_coordinates_and_excess(self):
        changes = [{'profile':'missing'}, {'cargo':[]}, {'cargo':[{'asset':'missing'}]},
                   {'cargo':[{'asset':'crate','position':[0,float('nan'),0]}]},
                   {'cargo':[{'asset':'crate','position':[True,0,0]}]}]
        for change in changes:
            with self.subTest(change=change),self.assertRaises(SupportError):
                configuration(resources(),{**spec(),**change})
        with tempfile.TemporaryDirectory() as tmp,self.assertRaises(ValueError):
            compile_assembly(resources(),'receiver','donor',{'support_vehicle':spec()},tmp,'mw_bad')

    def test_existing_service_replaced_instead_of_stacked(self):
        r=resources();key='entity/receiver/receiver.def'
        r.files[key]=r.files[key].replace(b'{mass 19000}',b'{extender "supply_zone" {limit 999}}{mass 19000}')
        _,root,_,_=self.compile(r)
        self.assertEqual(len([n for n in root.find_all('extender') if n.args==['supply_zone']]),1)

    def test_removed_turret_does_not_return_in_wreck_models(self):
        r=resources()
        for tag in ('x','xx'):
            r.files[f'entity/receiver_{tag}/receiver_{tag}.def']=r.files['entity/receiver/receiver.def']
            r.files[f'entity/receiver_{tag}/receiver_{tag}.mdl']=r.files['entity/receiver/receiver.mdl']
            for mesh in ('hull','tower','mantlet','barrel','coax'):
                r.files[f'entity/receiver_{tag}/{mesh}.ply']=eply()
        result,_,_,_=self.compile(r)
        self.assertEqual(len(result['metadata']['wrecks']),2)

    def test_catalog_discovers_effective_macro_profiles_and_never_offers_infinite_supply(self):
        with tempfile.TemporaryDirectory() as tmp:
            game=Path(tmp)/'game';mod=Path(tmp)/'mod'
            def write(root,path,text):
                dest=root/'resource'/path;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(text,encoding='utf-8')
            write(game,'properties/resupply.inc','(define "native_ammo" {extender "supply_zone" {limit %load}{radius 8}{resupply {item "x" "ammo" 10}}})')
            write(mod,'properties/resupply.inc','(define "native_ammo" {extender "supply_zone" {limit %load}{radius 12}{resupply {item "mod_round" "ammo" 10}}})')
            for name,amount in [('supply',500),('infinite',0)]:
                write(game,f'entity/-vehicle/test/{name}/{name}.def','{game_entity ("native_ammo" load('+str(amount)+'))}')
            r=ResourceIndex(game,[mod]).build()
            try:
                c=catalog(r)
                self.assertEqual(len(c['profiles']),1)
                self.assertEqual(c['profiles'][0]['radius'],12)
                self.assertIn('mod_round',c['profiles'][0]['body'])
                self.assertNotIn('body',public_catalog(r)['profiles'][0])
            finally:r.close()

    def test_campaign_preserves_owned_inventory_and_fuel_when_converting(self):
        from tests.test_campaign import SCENE,make_save
        from workshop.campaign import load_campaign,plan_edit,apply_plan
        result,_,_,_=self.compile()
        with tempfile.TemporaryDirectory() as tmp:
            snap=load_campaign(make_save(Path(tmp)/'copy.sav',SCENE))
            plan=plan_edit(snap,'0x9000','mw_support',1,state_profile=result['metadata']['state_profile'],keep_names=True)
            self.assertEqual(plan['consumed_eids'],['0x9000'])
            self.assertTrue(plan['reserve_squads'])
            text=apply_plan(snap,plan)['campaign.scn'].decode()
            self.assertIn('old_ammo',text)
            self.assertIn('mw_support',text)


if __name__ == '__main__':
    unittest.main()
