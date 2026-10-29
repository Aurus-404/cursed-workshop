import json
import tempfile
import struct
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from workshop.compiler import compile_assembly, _animation_map
from workshop.geometry import parse_model, point, descendants
from workshop.gun_assembly import discover, selected, preview_parts, GunError, merge_animations
from workshop.syntax import parse,block
from tests.test_geometry import eply


class Resources:
    sources=[]
    def __init__(self):
        self.files={}
        for name,ammo in (('receiver','short_round'),('donor','long_round'),('towed','long_round')):
            folder='entity/'+name+'/'
            self.files[folder+name+'.def']=('''{game_entity {extension "%s.mdl"}
              {mass 19000}{Chassis {speed 30}}
              {placer {place "driver" {group "crew"}}{place "gunner" {group "crew"}}{place "charger" {group "crew"}}}
              {Weaponry {place "main_slot" {LinkBone "breech"}{weapon "%s_weapon" filling "%s ap" 1}{gunner "gunner"}{charger "charger"}}
              {place "coax_slot" {type "coaxial"}{LinkBone "coax"}{weapon "mg" filling "mg_round belt" 50}{gunner "gunner"}{charger "charger"}}}
              {extender "inventory" {box {item "%s ap" 10}{item "%s he" 5}{item "mg_round belt" 500}{item "toolkit" 1}}}}
              '''%(name,name,ammo,ammo,ammo)).encode()
            self.files[folder+name+'.mdl']=b'''{skeleton {bone "frame" {bone "body" {VolumeView "hull.ply"}
              {bone revolute "azimuth" {VolumeView "tower.ply"}{Position 0 0 10}
              {bone revolute "elevation" {Position 5 0 2}{limits -10 20}{VolumeView "mantlet.ply"}
              {bone "breech" {VolumeView "barrel.ply"}{bone "sight" {Position 10 0 0}}}
              {bone "coax" {Position 0 2 0}{VolumeView "coax.ply"}}}}}}}
              {volume "barrel_volume" {bone "breech"}{box 10 1 1}}
              {volume "turret_volume" {bone "azimuth"}{box 3 3 3}}'''
            for mesh in ('hull','tower','mantlet','barrel','coax'):self.files[folder+mesh+'.ply']=eply()
            self.files['set/stuff/gun/'+name+'_weapon']=('{from "pattern gun" {filling "'+ammo+'"}}').encode()
        self.files['set/stuff/gun/gun.pattern']=b'{weapon_gun}'
        self.files['set/stuff/gun/mg']=b'{from "pattern gun" {filling "mg_round"}}'
        for family in ('short_round','long_round'):
            for kind in ('ap','he'):
                self.files[f'set/stuff/shell/{family}.{kind}.ammo']=('{ammo {entity "'+family+'_'+kind+'"}}').encode()
        self.files['properties/vehicle.ext']=b''
        # Towed piece has no coax; carriage/crew outside the elevation interface.
        text=self.files['entity/towed/towed.def'].decode()
        root=parse(text).find('game_entity');root.find('Weaponry').remove(root.find('Weaponry').find_all('place')[1])
        self.files['entity/towed/towed.def']=root.render().encode()
    @property
    def entries(self):return {p:SimpleNamespace(path=p) for p in self.files}
    def read(self,path):return self.files[path]
    def read_text(self,path):return self.read(path).decode()
    def exists(self,path):return path in self.files
    def resolve_template(self,name):return {'id':name,'definition_path':f'entity/{name}/{name}.def','model_path':f'entity/{name}/{name}.mdl','source_id':'fixture','props':['cannon'] if name=='towed' else []}
    resolve_entity=resolve_template
    def resolve_reference(self,identifier,path,suffixes=('',)):
        from workshop.geometry import resolve_path
        for suffix in suffixes:
            candidate=resolve_path(path,identifier+suffix)
            if self.exists(candidate):return candidate
        raise FileNotFoundError(identifier)


class GunAssemblyTests(unittest.TestCase):
    def test_shared_housing_follows_replacement_without_moving_coax_joint(self):
        for pivot in ([0,0,0],[3,0,1]):
            with self.subTest(pivot=pivot):
                resources=Resources()
                path='entity/receiver/receiver.mdl'
                resources.files[path]+=b'{volume "housing" {bone "elevation"}{box 2 2 2}}'
                before=parse_model(resources.files[path])
                result,root,model=self.compile(resources,transform={'turret':{'pivot':pivot}})
                housing=next(n for n in model['bones'] if n.startswith('cw_mantlet_'))
                main=root.find('Weaponry').find_all('place')[0].find('LinkBone').args[0]
                chain=[];current=main
                while current in model['bones']:
                    chain.append(current);current=model['bones'][current]['parent']
                joint=next(n for n in chain if model['bones'][n]['node'].args[0]=='revolute')
                self.assertIn(housing,descendants(model['bones'],joint))
                for actual,expected in zip(sum(model['bones'][housing]['world'],[]),sum(before['bones']['elevation']['world'],[])):
                    self.assertAlmostEqual(actual,expected,places=6)
                self.assertEqual(model['volumes']['housing']['bone'],housing)
                # The coax branch retains its world rest frame, even with a new pivot.
                for actual,expected in zip(sum(model['bones']['coax']['world'],[]),sum(before['bones']['coax']['world'],[])):
                    self.assertAlmostEqual(actual,expected,places=6)
                if any(pivot):self.assertEqual(model['bones']['elevation']['assets'],[])
                self.assertTrue(model['bones'][housing]['assets'][0].startswith('h_'))
                preview=preview_parts(resources,'receiver','donor',{'recipient_place':'main_slot','donor_place':'main_slot'})
                self.assertTrue(any(mesh['bone']=='elevation' for mesh in preview['hull']['meshes']))

    def test_ammo_family_ignores_the_container_prefix(self):
        # toxicman400: the Crocodile's "ammo flame_thrower" fuel is a whole family.
        from workshop.gun_assembly import _ammo_key
        self.assertEqual(_ammo_key('ammo flame_thrower'),'ammo flame_thrower')
        self.assertEqual(_ammo_key('ammo mgun_ger belt mk2'),'ammo mgun_ger belt')
        self.assertEqual(_ammo_key('bulletger_75_l43 he'),'bulletger_75_l43')

    def test_original_mantlet_can_be_left_out(self):
        resources=Resources()
        path='entity/receiver/receiver.mdl'
        resources.files[path]+=b'{volume "housing" {bone "elevation"}{box 2 2 2}}'
        spec={'schema':1,'recipient_place':'main_slot','donor_place':'main_slot','drop_mantlet':True}
        result,root,model=self.compile(resources,transform={'gun_swap':spec})
        self.assertFalse(any(n.startswith('cw_mantlet_') for n in model['bones']))
        self.assertNotIn('housing',model['volumes'])
        self.assertIn('coax',model['bones'])  # the coaxial weapon itself stays
        preview=preview_parts(resources,'receiver','donor',spec)
        self.assertFalse(any(mesh['bone']=='elevation' for mesh in preview['hull']['meshes']))

    def test_preserved_housing_survives_another_gun_replacement(self):
        resources=Resources()
        result,root,model=self.compile(resources,transform={'turret':{'pivot':[3,0,1]}})
        before={name:b['world'] for name,b in model['bones'].items() if name.startswith('cw_mantlet_')}
        folder=Path(result['output_dir'])/'resource/entity/-vehicle/workshop/mw_gun_test'
        for path in folder.iterdir():
            if path.is_file():resources.files['entity/receiver/'+path.name]=path.read_bytes()
        resources.files['entity/receiver/receiver.def']=root.render().encode()
        resources.files['entity/receiver/receiver.mdl']=model['document'].render().encode()
        _,_,after=self.compile(resources)
        for name,world in before.items():
            self.assertIn(name,after['bones'])
            for actual,expected in zip(sum(after['bones'][name]['world'],[]),sum(world,[])):
                self.assertAlmostEqual(actual,expected,places=6)

    def inverted_socket(self,resources,name):
        resources.files[f'entity/{name}/{name}.mdl']=b'''{skeleton {bone "frame" {bone "body" {VolumeView "hull.ply"}
          {bone revolute "elevation" {Position 5 0 12}{orientation 1 0 0 0 0 1 0 -1 0}{limits -8 22}
          {bone revolute "azimuth" {orientation 1 0 0 0 0 -1 0 1 0}{limits -12 12}
          {bone "mantlet" {Position 2 1 0}{VolumeView "mantlet.ply"}
          {bone "breech" {Position 3 0 0}{VolumeView "barrel.ply"}{bone "sight" {Position 10 0 0}}}}
          {bone "coax" {Position 0 2 0}{VolumeView "coax.ply"}}}}}}}
          {volume "barrel_volume" {bone "breech"}{box 10 1 1}}
          {volume "turret_volume" {bone "azimuth"}{box 3 3 3}}'''

    def test_inverted_coincident_socket_is_discovered_without_names_or_rest_pose_changes(self):
        r=Resources();self.inverted_socket(r,'receiver')
        source=r.files['entity/receiver/receiver.mdl'];native=parse_model(source)
        row=discover(r,'receiver')['weapons'][0]
        self.assertTrue(row['available']);self.assertEqual(row['pivot'],'elevation');self.assertEqual(row['yaw'],'azimuth')
        self.assertNotIn('azimuth',row['bones'])
        model=selected(r,'receiver','main_slot')[3]
        self.assertEqual(model['bones']['elevation']['parent'],'azimuth')
        self.assertEqual(model['bones']['azimuth']['parent'],'body')
        for name,bone in native['bones'].items():
            for actual,expected in zip(sum(model['bones'][name]['world'],[]),sum(bone['world'],[])):
                self.assertAlmostEqual(actual,expected,places=8)
        self.assertEqual(r.files['entity/receiver/receiver.mdl'],source)

    def test_inverted_socket_compiles_as_receiver_donor_and_both_with_yaw_and_coax_preserved(self):
        for inverted in [('receiver',),('donor',),('receiver','donor')]:
            with self.subTest(inverted=inverted):
                r=Resources()
                for name in inverted:self.inverted_socket(r,name)
                original=parse_model(r.files['entity/receiver/receiver.mdl'])
                data=preview_parts(r,'receiver','donor',{'recipient_place':'main_slot','donor_place':'main_slot'})
                self.assertTrue(data['preview_valid'])
                result,root,model=self.compile(r)
                self.assertTrue(result['compiled']);self.assertFalse(result['runtime_verified'])
                self.assertEqual(model['bones']['azimuth']['parent'],'body')
                self.assertEqual(model['bones']['elevation']['parent'],'azimuth')
                self.assertEqual(model['bones']['coax']['world'],original['bones']['coax']['world'])
                if 'receiver' in inverted:
                    self.assertEqual(model['bones']['azimuth']['node'].find('limits').args,['-12','12'])
                places={p.args[0]:p for p in root.find('Weaponry').find_all('place')}
                self.assertEqual(places['main_slot'].find('weapon').args[0],'donor_weapon')
                self.assertEqual(places['coax_slot'].find('weapon').args[0],'mg')
                self.assertEqual(root.find('mass').args,['19000'])
                self.assertEqual(result['metadata']['crew_roles'],['charger','driver','gunner'])

    def test_unverified_inverted_interfaces_remain_blocked(self):
        for replacement in [b'{Position 0 0 1}',b'{VolumeView "tower.ply"}']:
            r=Resources();self.inverted_socket(r,'receiver')
            key='entity/receiver/receiver.mdl'
            r.files[key]=r.files[key].replace(b'{bone revolute "azimuth"',b'{bone revolute "azimuth" '+replacement)
            self.assertFalse(discover(r,'receiver')['weapons'][0]['available'])
        r=Resources();self.inverted_socket(r,'receiver');key='entity/receiver/receiver.mdl'
        r.files[key]=r.files[key].replace(b'{bone revolute "azimuth"',b'{bone "other_branch" {VolumeView "tower.ply"}}{bone revolute "azimuth"')
        self.assertFalse(discover(r,'receiver')['weapons'][0]['available'])
        r=Resources();self.inverted_socket(r,'receiver')
        r.files[key]=r.files[key].replace(b'bone revolute "elevation"',b'bone "elevation"').replace(b'"azimuth"',b'"turret"')
        self.assertFalse(discover(r,'receiver')['weapons'][0]['available'])

    def test_filled_weapon_resolves_inventory_container_prefix_without_explicit_filling(self):
        r=Resources();r.files['entity/donor/donor.def']=r.files['entity/donor/donor.def'].replace(
            b'{weapon "donor_weapon" filling "long_round ap" 1}',b'{weapon "donor_weapon" filled}').replace(
            b'"long_round ap"',b'"ammo long_round ap"').replace(b'"long_round he"',b'"ammo long_round he"')
        result,root,model=self.compile(r)
        self.assertTrue(result['compiled']);self.assertIn('ammo long_round ap',root.render())
    def test_native_ammo_container_prefix_preserves_family_validation(self):
        from workshop.gun_assembly import _fillings,_mounted_ammo
        r=Resources();r.files['set/stuff/gun/mg']=b'{from "pattern gun" {filling "mgun_rus" 63}}'
        family,_=_fillings(r,block('{weapon "mg" filling "ammo mgun_rus dt" 63}'))
        self.assertEqual(family,'ammo mgun_rus')
        root=block('{game_entity {extender "inventory" {weapon "slot" {mask "mountable"}}{box {item "mg" filled {user "slot"}}}}}')
        self.assertIn('ammo mgun_rus',_mounted_ammo(r,root))
        with self.assertRaises(GunError):_fillings(r,block('{weapon "mg" filling "ammo mgun_ger belt" 63}'))
    def test_selected_weapon_can_live_in_a_later_weaponry_block(self):
        r=Resources();key='entity/receiver/receiver.def';doc=parse(r.files[key]);root=doc.find('game_entity')
        first=root.find('Weaponry');main=first.find_all('place')[0];first.remove(main)
        second=block('{Weaponry}');second.append(main);root.append(second);r.files[key]=doc.render().encode()
        result,emitted,_=self.compile(r)
        main=next(p for w in emitted.find_all('Weaponry') for p in w.find_all('place') if p.args[0]=='main_slot')
        self.assertEqual(main.find('weapon').args[0],'donor_weapon')

    def test_a_donor_bone_named_like_the_receiver_pivot_is_renamed(self):
        # Nb.Fz auxiliary MG pivots on "gun_rot1"; the leIG 18 brings its own
        # "gun_rot1" under its "gun_rot": both ended up with the same name.
        r=Resources();key='entity/donor/donor.mdl'
        r.files[key]=r.files[key].replace(b'bone revolute "elevation"',b'bone revolute "gun_rot"').replace(
            b'{bone "sight" {Position 10 0 0}}',b'{bone "sight" {Position 10 0 0}}{bone "elevation" {Position 1 0 0}}')
        result,root,model=self.compile(r)
        self.assertTrue(result['compiled'])
        self.assertIn('module_elevation',model['bones'])

    def test_new_gun_joint_limits_reach_model_and_definition(self):
        result,root,model=self.compile(Resources(),transform={'joint_limits':{'turret':{'elevation':[-5,35]}}})
        self.assertEqual(model['bones']['elevation']['node'].find('limits').args,['-5','35'])
        self.assertEqual(next(n for n in root.find_all('bone') if n.args[0]=='elevation').find('limits').args,['-5','35'])

    def test_imported_dual_gun_does_not_create_a_host_for_retained_weapon_slot(self):
        r = Resources()
        for suffix in ('def', 'mdl'):
            key = 'entity/towed/towed.' + suffix
            r.files[key] = r.files[key].replace(b'"breech"', b'"coax_slot"')
        key = 'entity/towed/towed.def'
        r.files[key] = r.files[key].replace(b'{place "main_slot"', b'{place "main_slot" {Barrels 2}')
        key = 'entity/towed/towed.mdl'
        r.files[key] = r.files[key].replace(b'{bone "sight" {Position 10 0 0}}',
            b'{bone "sight" {Position 10 0 0}}{bone "shooter00"}{bone "shooter01"}')
        result, root, model = self.compile(r, donor='towed')
        places = {p.args[0]: p for p in root.find('Weaponry').find_all('place')}
        self.assertNotIn('coax_slot', model['bones'])
        self.assertIn('module_coax_slot', model['bones'])
        self.assertEqual(places['coax_slot'].find('LinkBone').args, ['coax'])
        self.assertEqual(places['main_slot'].find('LinkBone').args, ['module_coax_slot'])
        self.assertEqual(places['main_slot'].find('Barrels').args, ['2'])
        self.assertEqual(model['bones']['shooter00']['parent'], 'module_coax_slot')
        self.assertEqual(model['bones']['shooter01']['parent'], 'module_coax_slot')

    def test_aiming_camera_outside_selected_gun_gets_a_geometry_free_socket(self):
        from workshop.geometry import multiply, inverse
        r = Resources()
        source = parse_model(r.files['entity/donor/donor.mdl'])
        profile = block('{native {level {linkBone "frame"}}'
                        '{level {zoom}{linkBone "AZIMUTH"}{offset 1 2 3}}}')
        with patch('workshop.registration._native_camera_profile', return_value=(profile, [])):
            result, root, model = self.compile(r)
        name = result['metadata']['camera_bone_mapping']['AZIMUTH']
        self.assertEqual(model['bones'][name]['parent'], 'azimuth')
        self.assertEqual(model['bones'][name]['assets'], [])
        expected = multiply(multiply(model['bones']['elevation']['world'],
                                     inverse(source['bones']['elevation']['world'])),
                            source['bones']['azimuth']['world'])
        for actual_row, expected_row in zip(model['bones'][name]['world'], expected):
            for actual, value in zip(actual_row, expected_row):
                self.assertAlmostEqual(actual, value, places=7)

    def test_commander_mount_hook_geometry_is_not_lost_before_export(self):
        def mount(resources,root,model,spec,assets,diagnostics):
            model['skeleton'].append(block('{bone "commander_socket"}'))
            root.find('Weaponry').append(block('{place "mgun_cmd" {LinkBone "commander_socket"}{type "aa"}}'))
            return {'operator':'commander','slot':'mgun_cmd'}
        with patch('workshop.commander_mg.apply_mount',side_effect=mount):
            result,root,model=self.compile(Resources(),transform={'commander_mg':{'fixture':True}})
        self.assertIn('commander_socket',model['bones'])
        self.assertEqual(result['metadata']['commander_mg']['slot'],'mgun_cmd')

    def test_single_horizontal_elevation_joint_does_not_require_a_turret_class(self):
        r=Resources()
        for name in ('receiver','donor'):
            model=f'entity/{name}/{name}.mdl';definition=f'entity/{name}/{name}.def'
            r.files[model]=r.files[model].replace(b'bone revolute "azimuth"',b'bone "azimuth"').replace(b'{bone revolute "elevation"',b'{bone revolute "elevation" {orientation 1 0 0 0 0 1 0 -1 0}').replace(b'"breech"',b'"gun"')
            r.files[definition]=r.files[definition].replace(b'"breech"',b'"gun"')
        result,_,model=self.compile(r)
        self.assertIn('azimuth',model['bones']);self.assertTrue(result['compiled'])

    def test_incomplete_animation_retarget_does_not_export_as_a_working_gun(self):
        from workshop.gun_assembly import _Assets
        original=_Assets.rewrite
        def rewrite(assets,*args,**kwargs):
            original(assets,*args,**kwargs)
            if not assets.diagnostics:assets.diagnostics.append({'code':'animation_tracks_dropped'})
        with patch.object(_Assets,'rewrite',rewrite),self.assertRaises(GunError):self.compile(Resources())

    def test_missing_shell_definition_is_rejected_before_export(self):
        r=Resources();del r.files['set/stuff/shell/long_round.he.ammo']
        with self.assertRaises(GunError):self.compile(r)

    def test_compiled_recipe_migrates_and_recompiles_with_real_campaign_planner(self):
        from tests.test_campaign import SCENE,make_save
        from workshop.campaign import load_campaign,plan_assembly,apply_plan
        r=Resources();result,_,_=self.compile(r)
        document=parse(SCENE.replace('tank_hull','receiver').replace('wheeled_donor','donor').replace('old_ammo','short_round ap'))
        for entity in document.find('campaign').find_all('Entity'):
            for child in list(entity.children):
                if child.head in ('Armor','Component'):entity.remove(child)
        with tempfile.TemporaryDirectory() as temp:
            snapshot=load_campaign(make_save(Path(temp)/'copied.sav',document.render()))
            plan=plan_assembly(snapshot,'0x9000','0x9010','mw_gun',3,state_profile=result['metadata']['state_profile'])
            migrated=apply_plan(snapshot,plan)['campaign.scn']
            self.assertIn(b'long_round ap',migrated);self.assertNotIn(b'short_round ap',migrated)
            rebuilt=compile_assembly(r,result['metadata']['recipient'],result['metadata']['donor'],result['metadata']['transform'],Path(temp)/'rebuild','mw_gun_again')
            self.assertEqual(rebuilt['metadata']['gun_assembly'],result['metadata']['gun_assembly'])

    def test_receiver_seat_under_elevation_remains_recipient_owned(self):
        r=Resources();key='entity/receiver/receiver.mdl'
        r.files[key]=r.files[key].replace(b'{bone "coax"',b'{bone "seat_anchor" {Position -2 1 0}}{bone "coax"')
        r.files['entity/receiver/receiver.def']=r.files['entity/receiver/receiver.def'].replace(b'place "gunner" {group "crew"}',b'place "gunner" {group "crew"}{LinkBone "seat_anchor"}')
        result,root,model=self.compile(r)
        self.assertIn('seat_anchor',model['bones'])
        self.assertEqual(model['bones']['seat_anchor']['parent'],'elevation')
        self.assertEqual(point(model['bones']['seat_anchor']['world'],[0,0,0]),[3,1,12])

    def test_inherited_muzzle_is_explicitly_remapped(self):
        r=Resources();r.files['set/stuff/gun/gun.pattern']=b'{weapon_gun {foresight "SIGHT"}}'
        result,root,model=self.compile(r)
        main=root.find('Weaponry').find_all('place')[0]
        self.assertEqual(main.find('foresight').args,['sight'])
        self.assertIn('sight',model['bones'])

    def test_donor_worker_minimum_brings_the_missing_loaders(self):
        r=Resources();r.files['entity/towed/towed.def']=r.files['entity/towed/towed.def'].replace(b'charger "charger"',b'charger "charger charger2" minWorkers 2')
        result,root,model=self.compile(r,'towed')
        main=root.find('Weaponry').find_all('place')[0]
        self.assertEqual(main.find('charger').args,['charger charger2','minWorkers','2'])
        self.assertIn('charger2',result['metadata']['crew_roles'])
        seat=next(p for p in root.walk('place') if p.args==['charger2'] and p.find('group'))
        self.assertEqual(seat.find('group').args,['crew'])
        self.assertIn(seat.find('linkbone').args[0],model['bones'])

    def test_sparse_animation_merge_keeps_disjoint_native_tracks(self):
        def clip(name,frame):
            encoded=name.encode()
            return b'EANM'+struct.pack('<I',1)+b'FRMS'+struct.pack('<I',20)+b'BMAP'+struct.pack('<I',1)+struct.pack('<I',len(encoded))+encoded+b'FRM2'+struct.pack('<HB',frame,1)+struct.pack('<BH3f',0,1,1,2,3)
        merged=merge_animations(clip('coax',2),clip('new_barrel',7))
        self.assertEqual(_animation_map(merged)[0],['coax','new_barrel'])
        with self.assertRaises(GunError):merge_animations(clip('same',2),clip('same',7))

    def test_donor_recoil_gets_its_own_sequence_when_the_receiver_name_is_taken(self):
        # T-29: three guns changed and every "recoil" merged into one clip, so
        # any shot recoiled all three. The new gun keeps a clip of its own.
        def clip(name,frame):
            encoded=name.encode()
            return b'EANM'+struct.pack('<I',1)+b'FRMS'+struct.pack('<I',20)+b'BMAP'+struct.pack('<I',1)+struct.pack('<I',len(encoded))+encoded+b'FRM2'+struct.pack('<HB',frame,1)+struct.pack('<BH3f',0,1,1,2,3)
        r=Resources()
        for name,bone in (('receiver','coax'),('donor','breech')):
            key=f'entity/{name}/{name}.mdl'
            r.files[key]=r.files[key].replace(b'{skeleton ',b'{skeleton {animation {sequence "recoil" {file "recoil.anm"}}} ')
            r.files[f'entity/{name}/recoil.anm']=clip(bone,3)
        result,root,model=self.compile(r)
        contract=result['metadata']['gun_assembly']
        self.assertEqual(contract['sequence_names'],{'recoil':'recoil_main_slot'})
        sequences={s.args[0]:s.find('file').args[0] for s in model['document'].walk('sequence') if s.args}
        self.assertEqual(set(sequences),{'recoil','recoil_main_slot'})
        folder=Path(result['output_dir'])/'resource/entity/-vehicle/workshop/mw_gun_test'
        tracks=lambda name:_animation_map((folder/sequences[name]).read_bytes())[0]
        self.assertNotIn('breech',tracks('recoil'),'the receiver clip never moves the new gun')
        self.assertTrue(tracks('recoil_main_slot'),'the new gun recoils through its own clip')

    def test_a_gun_laid_on_its_side_keeps_a_level_elevation_joint(self):
        # Matilda + 2-pounder rolled 90 degrees: the joint rolled with it and the
        # elevation became a traverse. The joint keeps its axes; the model rolls.
        from workshop.geometry import multiply,inverse
        _,_,rolled=self.compile(Resources(),transform={'turret':{'position':[0,0,0],'rotation':[90,0,0]}})
        _,_,native=self.compile(Resources())
        joint=lambda model:next(n for n in model['bones'] if model['bones'][n]['parent']=='azimuth' and n!='coax' and not n.startswith('cw_'))
        same=lambda a,b:all(abs(a[r][c]-b[r][c])<1e-6 for r in range(4) for c in range(4))
        self.assertTrue(same(rolled['bones'][joint(rolled)]['world'],native['bones'][joint(native)]['world']),'the elevation joint keeps its native axes')
        fit=[n for n in rolled['bones'] if n.startswith('cw_fit_')]
        self.assertEqual(len(fit),1)
        roll=[[1,0,0,0],[0,0,-1,0],[0,1,0,0],[0,0,0,1]]
        self.assertTrue(same(rolled['bones']['breech']['world'],
                             multiply(multiply(native['bones'][joint(native)]['world'],roll),multiply(inverse(native['bones'][joint(native)]['world']),native['bones']['breech']['world']))),
                        'the barrel is laid on its side exactly where it was placed')
        _,_,turned=self.compile(Resources(),transform={'turret':{'position':[0,0,0],'rotation':[0,0,30]}})
        self.assertFalse([n for n in turned['bones'] if n.startswith('cw_fit_')],'a horizontal turn still turns the joint itself')

    def compile(self,resources,donor='donor',transform=None):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        tr={'gun_swap':{'schema':1,'recipient_place':'main_slot','donor_place':'main_slot'}}
        tr.update(transform or {})
        result=compile_assembly(resources,'receiver',donor,tr,temp.name,'mw_gun_test')
        folder=Path(temp.name)/'resource/entity/-vehicle/workshop/mw_gun_test'
        return result,parse((folder/'mw_gun_test.def').read_bytes()).find('game_entity'),parse_model((folder/'mw_gun_test.mdl').read_bytes())

    def test_vehicle_weapon_preserves_turret_secondary_operators_and_ammo(self):
        r=Resources();result,root,model=self.compile(r)
        self.assertIn('azimuth',model['bones']);self.assertIn('coax',model['bones'])
        self.assertEqual(model['bones']['azimuth']['assets'][0][0],'h')
        self.assertEqual(point(model['bones']['coax']['world'],[0,0,0]),[5,2,12])
        self.assertEqual(model['bones']['coax']['parent'],'elevation')
        places={p.args[0]:p for p in root.find('Weaponry').find_all('place')}
        self.assertEqual(places['main_slot'].find('weapon').args[0],'donor_weapon')
        self.assertEqual(places['coax_slot'].find('weapon').args[0],'mg')
        self.assertEqual(places['main_slot'].find('charger').args,['charger'])
        text=root.render();self.assertIn('mg_round belt',text);self.assertIn('long_round ap',text);self.assertNotIn('short_round ap',text)
        self.assertEqual(result['metadata']['crew_roles'],['charger','driver','gunner'])
        self.assertTrue(result['compiled']);self.assertFalse(result['runtime_verified'])

    def test_towed_weapon_does_not_import_carriage_or_donor_crew(self):
        r=Resources();r.files['entity/towed/towed.mdl']=r.files['entity/towed/towed.mdl'].replace(b'{bone "body"',b'{bone "body" {bone "transport_wheels" {VolumeView "hull.ply"}}')
        result,root,model=self.compile(r,'towed')
        self.assertNotIn('transport_wheels',model['bones'])
        self.assertIn('coax',model['bones']);self.assertEqual(result['metadata']['crew_slots'],3)
        self.assertEqual(root.find('mass').args,['19000'])

    def test_discovery_uses_place_links_and_reports_invalid_weapons(self):
        r=Resources();rows=discover(r,'receiver')['weapons']
        self.assertTrue(next(x for x in rows if x['place']=='main_slot')['available'])
        r.files['entity/donor/donor.def']=r.files['entity/donor/donor.def'].replace(b'LinkBone "breech"',b'LinkBone "absent"')
        self.assertFalse(discover(r,'donor')['weapons'][0]['available'])
        with self.assertRaises(GunError):self.compile(r)

    def test_preview_preserves_receiver_turret_and_excludes_donor_turret(self):
        r=Resources();data=preview_parts(r,'receiver','donor',{'recipient_place':'main_slot','donor_place':'main_slot'})
        self.assertTrue(data['preview_valid']);self.assertFalse(data['runtime_verified'])
        self.assertIn('azimuth',[m['bone'] for m in data['hull']['meshes']])
        self.assertNotIn('azimuth',[m['bone'] for m in data['turret']['meshes']])
        self.assertNotIn('coax',[m['bone'] for m in data['turret']['meshes']])

    def test_adjustment_is_about_the_receiver_mount_not_world_origin(self):
        result,root,model=self.compile(Resources(),transform={'turret':{'position':[2,3,4],'rotation':[0,0,90]}})
        self.assertEqual([round(v,6) for v in point(model['bones']['elevation']['world'],[0,0,0])],[7,3,16])

    def test_recipe_round_trip_contains_selection_and_source_provenance(self):
        result,_,_=self.compile(Resources())
        metadata=json.loads(json.dumps(result['metadata']))
        self.assertEqual(metadata['transform']['gun_swap']['donor_place'],'main_slot')
        self.assertIn('set/stuff/gun/donor_weapon',metadata['source_hashes'])

    def test_inconsistent_filling_is_rejected(self):
        r=Resources();r.files['entity/donor/donor.def']=r.files['entity/donor/donor.def'].replace(b'long_round ap',b'wrong_round ap')
        with self.assertRaisesRegex(GunError,'filling'):self.compile(r)

    def test_a_gun_changed_on_a_receiver_with_a_reversed_elevation_axis_stands_upright(self):
        # Aurus, Pz.Kpfw Nb.Fz: its elevation joints turn about the right of the
        # barrel, every native gun's about the left. Changed guns came out belly
        # up, and their own limits read reversed on the level joint.
        from workshop.gun_assembly import _upright, _flip_limits
        from workshop.geometry import euler_matrix, identity
        from workshop.syntax import block
        native=[[1,0,0,0],[0,0,-1,0],[0,1,0,0],[0,0,0,1]]   # X forward, Z (axis) to the left
        nbfz=[[1,0,0,0],[0,0,1,0],[0,-1,0,0],[0,0,0,1]]     # X forward, Z (axis) to the right
        self.assertEqual(_upright(native,native),identity())
        flip=_upright(nbfz,native)
        self.assertNotEqual(flip,identity())
        up=[sum(nbfz[2][k]*flip[k][c] for k in range(3)) for c in range(3)]
        donor_up=[native[2][c] for c in range(3)]
        self.assertGreater(sum(a*b for a,b in zip(up,donor_up)),0,'turned upright the donor keeps its own up')
        joint=block('{bone "gun_rot" {limits -4 75} {speed2 8}}')
        _flip_limits(joint)
        self.assertEqual(joint.find('limits').args,['-75','4'])

    def test_barrel_only_keeps_the_barrels_own_mesh(self):
        # TheOppsAreOntoMe: an emplacement's breech, cradle and shield stick out
        # of a vehicle turret. The barrel is the piece under the elevation pivot.
        from workshop.gun_assembly import _barrel
        model=parse_model('{skeleton {bone "basis" {bone revolute "gun_rot" {VolumeView "cradle.ply"}'
                          '{bone "gun" {VolumeView "tube.ply"} {bone "breechblock" {VolumeView "breech.ply"}} {bone "foresight1"}}}}}')
        owned={'gun_rot','gun','breechblock','foresight1'}
        self.assertEqual(_barrel(model,'gun',owned),['gun'])
        bare=parse_model('{skeleton {bone "basis" {bone revolute "gun_rot" {bone "gun" {bone "tube" {VolumeView "tube.ply"}}}}}}')
        self.assertEqual(_barrel(bare,'gun',{'gun_rot','gun','tube'}),['gun','tube'],'a bare barrel bone keeps its branch')
        self.assertIsNone(_barrel(model,None,owned))
