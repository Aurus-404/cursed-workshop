import json
import tempfile
import unittest
from pathlib import Path

from tests.test_gun_assembly import Resources
from tests.test_geometry import eply
from workshop.extra_armor import apply, preview, configuration, _volumes, _contract, _default_material, ArmorError
from workshop.compiler import compile_assembly
from workshop.geometry import parse_model, point
from workshop.syntax import parse, block


def resources():
    r = Resources();folder = 'entity/armor/'
    r.files[folder+'armor.def'] = b'{game_entity {extension "armor.mdl"}}'
    r.files[folder+'armor.mdl'] = b'{skeleton {bone "base" {bone "panel" {parameters "id=body;"}{Position 12 0 0}{VolumeView "plate.ply"}}}}{volume "body" {bone "panel"}{box 2 3 1}}'
    r.files[folder+'plate.ply'] = eply()
    r._armor_catalog = [{'id':'native_track','label':'Native track','kind':'tracks','source':'fixture',
        'definition_path':folder+'armor.def','model_path':folder+'armor.mdl','bone':'panel','volumes':['body'],
        'contracts':{'body':'{volume "source" {thickness 20}{material "steel"}{CastSteel 0.5}{tags "armor"}}'},
        'materials':['steel'],'thickness':[20.0],'cast_steel':True}]
    return r


def row(parent='azimuth'):
    return {'asset':'native_track','parent':parent,'position':[5,10,15],'rotation':[0,0,90]}


class ExtraArmorTests(unittest.TestCase):
    def test_full_and_selected_fittings_preserve_source_faces_on_reflected_hosts(self):
        from workshop.geometry import read_mesh
        reflect=b'{orientation 1 0 0 0 -1 0 0 0 1}'
        for whole in (False,True):
            for reflected in (False,True):
                with self.subTest(whole=whole,reflected=reflected),tempfile.TemporaryDirectory() as tmp:
                    r=resources();r.files['entity/armor/armor.mdl']=r.files['entity/armor/armor.mdl'].replace(b'{bone "base"',b'{bone "base" '+reflect)
                    if whole:r._armor_catalog[0]['bone']=None
                    if reflected:r.files['entity/receiver/receiver.mdl']=r.files['entity/receiver/receiver.mdl'].replace(b'{bone "frame"',b'{bone "frame" '+reflect)
                    compile_assembly(r,'receiver','receiver',{'commander_only':True,'extra_armor':[row('body')]},tmp,'mw_faces')
                    folder=Path(tmp)/'resource/entity/-vehicle/workshop/mw_faces';model=parse_model((folder/'mw_faces.mdl').read_bytes())
                    bone=next(b for name,b in model['bones'].items() if name.startswith('mw_armor_0_b') and b['assets'])
                    actual=read_mesh((folder/bone['assets'][0]).read_bytes());original=read_mesh(eply())
                    # Full models retain their reflected source basis; extracted
                    # subtrees remove it. Both must retain the native front side.
                    flip=reflected if whole else not reflected
                    self.assertEqual(actual['indices'],[0,2,1] if flip else original['indices'])
                    self.assertEqual(actual['uv'],original['uv'])
                    self.assertEqual(actual['normals'],original['normals'])
                    self.assertEqual(actual['positions'],original['positions'])

    def compile(self, transform, r=None, donor='receiver'):
        transform={'recipient_mount':'azimuth','donor_mount':'azimuth',**transform}
        with tempfile.TemporaryDirectory() as tmp:
            result = compile_assembly(r or resources(),'receiver',donor,transform,tmp,'mw_extra')
            folder=Path(tmp)/'resource/entity/-vehicle/workshop/mw_extra'
            root=parse((folder/'mw_extra.def').read_bytes()).find('game_entity')
            model=parse_model((folder/'mw_extra.mdl').read_bytes())
            recipe=json.loads((Path(tmp)/'assembly_mw_extra.json').read_text(encoding='utf8'))
            return result,root,model,recipe

    def test_full_vehicle_keeps_original_armor_turret_weapons_and_adds_local_volumes(self):
        result,root,model,_=self.compile({'commander_only':True,'extra_armor':[row()]})
        self.assertIn('azimuth',model['bones']);self.assertIn('barrel_volume',model['volumes'])
        self.assertEqual(root.find('mass').args,['19000'])
        self.assertEqual(len(root.find('Weaponry').find_all('place')),2)
        self.assertEqual(model['bones']['mw_armor_0_root']['parent'],'azimuth')
        volume=next(v for v in root.find_all('volume') if v.args==['mw_armor_0_v0'])
        self.assertEqual(volume.find('thickness').args,['20'])
        self.assertEqual(volume.find('CastSteel').args,['0.5'])
        self.assertEqual(volume.find('tags').args,['armor'])
        self.assertFalse(volume.find('component'))
        self.assertFalse(result['runtime_verified'])
        self.assertEqual(result['metadata']['state_profile']['weaponry_policy'],'reset')

    def test_combination_and_gun_swap_use_same_fitting_contract(self):
        for transform in ({}, {'gun_swap':{'schema':1,'recipient_place':'main_slot','donor_place':'main_slot'}}):
            with self.subTest(transform=transform):
                result,_,model,_=self.compile({**transform,'extra_armor':[row()]},donor='donor')
                self.assertIn('mw_armor_0_v0',model['volumes'])
                self.assertIn('mw_armor_0_v0',result['metadata']['state_profile']['allowed_damage_ids'])

    def test_multiple_fittings_have_unique_names_and_independent_local_frames(self):
        a=row('body');b=row('azimuth');b['position']=[-5,3,9]
        _,_,model,_=self.compile({'commander_only':True,'extra_armor':[a,b]})
        self.assertEqual(len([v for v in model['volumes'] if v.startswith('mw_armor_')]),2)
        self.assertNotEqual(point(model['bones']['mw_armor_0_root']['world'],[0,0,0]),point(model['bones']['mw_armor_1_root']['world'],[0,0,0]))

    def test_preview_and_export_share_centering_and_rotation(self):
        r=resources();pv=preview(r,'native_track')
        _,_,model,_=self.compile({'commander_only':True,'extra_armor':[row()]},r)
        from workshop.geometry import euler_matrix,multiply
        expected=multiply(model['bones']['azimuth']['world'],euler_matrix(row()['rotation'],row()['position']))
        a=point(expected,pv['meshes'][0]['positions'][:3])
        from workshop.geometry import read_mesh
        b=point(model['bones']['mw_armor_0_b0']['world'],read_mesh(eply())['positions'][:3])
        for x,y in zip(a,b):self.assertAlmostEqual(x,y,places=5)

    def test_recipe_roundtrip_and_disable_have_no_default_asset_changes(self):
        transform={'commander_only':True,'extra_armor':[row()]}
        result,_,_,recipe=self.compile(transform)
        again,_,_,_=self.compile(recipe['metadata']['transform'])
        self.assertEqual(result['metadata']['file_hashes'],again['metadata']['file_hashes'])
        a,_,_,_=self.compile({'commander_only':True})
        b,_,model,_=self.compile({'commander_only':True,'extra_armor':[]})
        self.assertEqual(a['metadata']['file_hashes'],b['metadata']['file_hashes'])
        self.assertFalse(any(n.startswith('mw_armor_') for n in model['bones']))

    def test_rejects_missing_parent_asset_and_invalid_pose_without_silent_decoration(self):
        for invalid in (None,{},[{'asset':'missing'}],[{**row(),'position':[True,0,0]}],
                        [{**row(),'position':[0,float('nan'),0]}]):
            with self.subTest(invalid=invalid),self.assertRaises(ArmorError):configuration(resources(),invalid)
        with self.assertRaises(ArmorError):self.compile({'commander_only':True,'extra_armor':[row('breech')]})

    def test_native_positional_macro_and_sand_material_contract(self):
        r=resources();r.files['properties/vehicle.ext']=b'(define "shield_track" {volume %0 {thickness %1}{tags "shield"}{CastSteel 0.5}})'
        root=block('{game_entity (include "/properties/vehicle.ext") ("shield_track" args "spare" 20)}')
        volumes=_volumes(r,root)
        self.assertEqual(volumes['spare'].find('thickness').args,['20'])
        self.assertEqual(_contract(volumes['spare'],'steel').find('CastSteel').args,['0.5'])
        sand=_contract(block('{volume "body" {thickness 60}{tags "body"}}'),'sand')
        self.assertEqual(sand.find('material').args,['sand'])
        self.assertEqual(sand.find('tags').args,['armor'])

    def test_material_inheritance_does_not_turn_mod_wood_into_steel(self):
        r=resources();r.files['properties/base.ext']=b'{props "steel" "vehicle"}'
        r.files['properties/wood.ext']=b'(include "base.ext"){props "wood"}'
        self.assertEqual(_default_material(r,block('{game_entity (include "/properties/wood.ext")}')),'wood')
        self.assertEqual(_default_material(r,block('{game_entity (include "/properties/wood.ext"){material "native_custom"}}')),'native_custom')

    def test_existing_fittings_are_kept_and_new_names_cannot_collide(self):
        r=resources();path='entity/receiver/receiver.mdl';doc=parse(r.files[path])
        doc.find('skeleton').append(block('{bone "mw_armor_0_root"}'));r.files[path]=doc.render().encode()
        _,_,model,_=self.compile({'commander_only':True,'extra_armor':[row()]},r)
        self.assertIn('mw_armor_0_root',model['bones'])
        self.assertIn('mw_armor_1_root',model['bones'])


    def test_support_conversion_keeps_cargo_and_can_add_separate_armor(self):
        from tests.test_support_vehicle import resources as support_resources, spec
        r=support_resources();armor=resources();r.files.update({k:v for k,v in armor.files.items() if k.startswith('entity/armor/')})
        r._armor_catalog=armor._armor_catalog
        _,root,model,_=self.compile({'support_vehicle':spec(),'extra_armor':[row('body')]},r)
        self.assertNotIn('azimuth',model['bones'])
        self.assertIn('mw_cargo_0',model['bones'])
        self.assertIn('mw_armor_0_v0',model['volumes'])
        self.assertEqual(len([n for n in root.find_all('extender') if n.args==['supply_zone']]),1)

    def test_catalog_uses_real_local_include_contract_and_excludes_shared_engine_geometry(self):
        from workshop.resources import ResourceIndex
        from workshop.extra_armor import catalog,resolve_fitting
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            game=Path(tmp)
            def write(path,value):
                target=game/'resource'/path;target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(value.encode() if isinstance(value,str) else value)
            write('properties/vehicle.ext','{props "steel"}(define "shield_track" {volume %0 {thickness %1}{tags "shield"}{CastSteel 0.5}})')
            prefix='entity/-vehicle/test/unusual/'
            write(prefix+'unusual.def','{game_entity (include "/properties/vehicle.ext")(include "armor.ext"){extension "unusual.mdl"}}')
            write(prefix+'armor.ext','("shield_track" args "v" 18)("shield_track" args "bad" 30)')
            write(prefix+'unusual.mdl','{skeleton {bone "body" {bone "spare" {VolumeView "plate.ply"}}{bone "unsafe" {VolumeView "plate.ply"}{bone "engine"}}}}{volume "v" {bone "spare"}{box 1 2 3}}{volume "bad" {bone "unsafe"}{box 2 3 4}}')
            write(prefix+'plate.ply',eply());r=ResourceIndex(game,[]).build()
            try:
                identifier=prefix+'unusual.mdl#spare'
                with patch('workshop.extra_armor.catalog',side_effect=AssertionError('Selected armor must not enumerate unrelated vehicles')):
                    direct=resolve_fitting(r,identifier)
                    self.assertEqual(direct['thickness'],[18.0])
                    self.assertIs(resolve_fitting(r,identifier),direct)
                    self.assertEqual(configuration(r,[dict(row(),asset=identifier)])[0][1],direct)
                items=catalog(r)
                self.assertEqual(len(items),1)
                self.assertEqual(direct,items[0],'Targeted and complete extraction have identical protection contracts')
                self.assertEqual(items[0]['kind'],'tracks')
                self.assertEqual(items[0]['thickness'],[18.0])
                self.assertEqual(preview(r,items[0]['id'])['preview_valid'],True)
                with self.assertRaises(ArmorError):configuration(r,[dict(row(),asset=prefix+'unusual.mdl#unsafe')])
                write(prefix+'armor.ext','("shield_track" args "v" 27)("shield_track" args "bad" 30)')
                r.build()
                self.assertEqual(resolve_fitting(r,identifier)['thickness'],[27.0],'Reindex clears previous protection contracts')
            finally:r.close()


if __name__=='__main__':unittest.main()
