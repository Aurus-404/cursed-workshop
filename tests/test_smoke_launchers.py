import json
import math
import tempfile
import unittest
from pathlib import Path

from tests.test_gun_assembly import Resources
from workshop.smoke_launchers import (configuration, geometry, mesh_bytes, texture_bytes,
                                      preview, apply, add_interactions, NATIVE, SmokeError)
from workshop.geometry import read_mesh, mesh_materials, parse_model, point, multiply, euler_matrix
from workshop.compiler import compile_assembly
from workshop.syntax import block, parse


def resources():
    r=Resources()
    r.files[NATIVE]=b'''{"tank" (define "smoke_shot" {spawn %0 "%1" x {impulse forward %2 %3 rnd_dir %3 %3}{call "activate"}})}
        {"smoke nwg" {on "smoke_screen" ("smoke_shot" args "nbk39" smoke_shot1 -17 0.5 0 20)}}'''
    r.files['set/stuff/grenade/smoke grenade/nbks.grenade']=b'{from "pattern smoke" {entity "nbks"}}'
    r.files['entity/fx/flash/flash_grenade_launcher.def']=b'{entity "fx"}'
    r.files['entity/nbk39/nbk39.def']=b'{game_entity {extension "nbk39.mdl"}}'
    r.files['entity/nbk39/nbk39.mdl']=b'{skeleton {bone "body"}}'
    return r


def row(count=3,parent='azimuth'):
    return {'tubes':count,'parent':parent,'position':[2,3,4],'rotation':[0,0,90]}


class SmokeLauncherTests(unittest.TestCase):
    def test_native_faces_remain_outward_on_both_host_handednesses(self):
        from workshop.geometry import linear_determinant
        def signs(mesh):
            for k in range(0,len(mesh['indices']),3):
                a,b,c=mesh['indices'][k:k+3];p=[mesh['positions'][j*3:j*3+3] for j in (a,b,c)]
                u=[p[1][j]-p[0][j] for j in range(3)];v=[p[2][j]-p[0][j] for j in range(3)]
                cross=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]]
                yield sum(x*y for x,y in zip(cross,mesh['normals'][a*3:a*3+3]))
        for count in (1,2,3):
            self.assertTrue(all(dot<0 for dot in signs(read_mesh(mesh_bytes(count)))))
            self.assertTrue(all(dot>0 for dot in signs(preview(count)['meshes'][0])))
        for reflected in (False,True):
            r=resources()
            if reflected:r.files['entity/receiver/receiver.mdl']=r.files['entity/receiver/receiver.mdl'].replace(b'{bone "frame"',b'{bone "frame" {orientation 1 0 0 0 -1 0 0 0 1}')
            _,_,model,files=self.compile(r,{'commander_only':True})
            for name,bone in model['bones'].items():
                if not name.startswith('cw_smoke_') or not bone['assets']:continue
                mesh=read_mesh(files[bone['assets'][0]]);det=linear_determinant(bone['world'])
                self.assertTrue(all(dot*det>0 for dot in signs(mesh)))

    def compile(self,r=None,transform=None,donor='receiver'):
        transform={'recipient_mount':'azimuth','donor_mount':'azimuth',**(transform or {}),'smoke_launchers':[row(1),row(2),row(3,'body')]}
        with tempfile.TemporaryDirectory() as tmp:
            result=compile_assembly(r or resources(),'receiver',donor,transform,tmp,'mw_smoke')
            folder=Path(tmp)/'resource/entity/-vehicle/workshop/mw_smoke'
            root=parse((folder/'mw_smoke.def').read_bytes()).find('game_entity')
            model=parse_model((folder/'mw_smoke.mdl').read_bytes())
            return result,root,model,{p.name:p.read_bytes() for p in folder.iterdir()}

    def test_original_hollow_meshes_have_native_material_uv_normals_and_tangents(self):
        previous=0
        for count in (1,2,3):
            raw=mesh_bytes(count);mesh=read_mesh(raw)
            self.assertGreater(len(mesh['indices']),previous);previous=len(mesh['indices'])
            self.assertEqual(mesh_materials(raw)[0][1],'cw_smoke_gray.mtl')
            self.assertTrue(all(math.isfinite(v) for v in mesh['positions']))
            self.assertEqual(len(mesh['uv']),mesh['vertex_count']*2)
            self.assertEqual(len(mesh['normals']),mesh['vertex_count']*3)
            self.assertLess(len(raw),50000)

    def test_texture_is_original_opaque_gray_with_all_mips(self):
        from workshop.textures import decode_dds
        for limit in (64,32,4,1):
            w,h,rgba=decode_dds(texture_bytes(),limit)
            self.assertLessEqual(w,limit);self.assertEqual(w,h)
            self.assertEqual(len(rgba),w*h*4)
            self.assertTrue(all(a==255 for a in rgba[3::4]))

    def test_three_options_have_correct_muzzle_count_and_launch_direction(self):
        from workshop.textures import decode_dds
        w,h,rgba=decode_dds(texture_bytes())
        paint={tuple(rgba[(y*w+x)*4:(y*w+x)*4+4]) for y in range(h) for x in range(w//2-1)}
        self.assertEqual(len(paint),1,'matte paint must not have a visible block/dither grid')
        for count in (1,2,3):
            data=preview(count)
            self.assertEqual(len(data['muzzles']),count)
            for direction in data['directions']:
                self.assertGreater(direction[0],.6);self.assertGreater(direction[2],.6)
            self.assertFalse(data['runtime_verified'])

    def test_full_edit_retains_weapons_turret_crew_and_mobility(self):
        result,root,model,files=self.compile(transform={'commander_only':True})
        self.assertIn('azimuth',model['bones']);self.assertIn('coax',model['bones'])
        self.assertEqual(root.find('mass').args,['19000'])
        self.assertEqual(len(root.find('Weaponry').find_all('place')),2)
        self.assertEqual(result['metadata']['crew_slots'],3)
        self.assertEqual(len(result['metadata']['smoke_launchers']['mounts']),3)
        self.assertIn('cw_smoke_gray.dds',files)
        self.assertEqual(len([n for n in root.find_all('extender') if n.args==['smokescreen']]),1)

    def test_geometry_and_launch_frames_follow_same_local_parent(self):
        result,_,model,_=self.compile(transform={'commander_only':True})
        for mount in result['metadata']['smoke_launchers']['mounts']:
            frame=multiply(model['bones'][mount['parent']]['world'],euler_matrix(mount['rotation'],mount['position']))
            expected=preview(mount['tubes'])
            for i,muzzle in enumerate(mount['muzzles']):
                bone=model['bones'][muzzle['bone']]
                for a,b in zip(point(bone['world'],[0,0,0]),point(frame,expected['muzzles'][i])):self.assertAlmostEqual(a,b,places=5)
                # Native negative-forward impulse must go OUT through the mouth.
                launch=[-bone['world'][axis][0] for axis in range(3)]
                direction=expected['directions'][i]
                world=[sum(frame[axis][j]*direction[j] for j in range(3)) for axis in range(3)]
                for a,b in zip(launch,world):self.assertAlmostEqual(a,b,places=5)

    def test_interactions_keep_original_handler_and_only_launch_emitted_bones(self):
        result,_,model,_=self.compile(transform={'commander_only':True})
        contract=result['metadata']['smoke_launchers'];record=block('{"mw_smoke tank" {on "smoke_screen" {tags add "native_marker"}}}')
        add_interactions(record,contract)
        handler=record.find_all('on')[0];self.assertIn('native_marker',handler.render())
        shots=[n for n in handler.walk('spawn') if n.args[0]=='nbk39']
        self.assertEqual(len(shots),6)
        for shot in shots:self.assertIn(shot.args[1],model['bones']);self.assertTrue(shot.find('call'))
        self.assertEqual(len([n for n in record.find_all('on') if n.args[0]=='smoke_screen']),1)

    def test_turret_and_gun_replacement_routes_use_same_contract(self):
        for tr in ({},{'gun_swap':{'schema':1,'recipient_place':'main_slot','donor_place':'main_slot'}}):
            result,root,model,_=self.compile(transform=tr,donor='donor')
            self.assertIn('cw_smoke_0_root',model['bones'])
            self.assertIn('smoke_launchers',result['metadata']['state_profile'])
            self.assertIn('coax',model['bones'])

    def test_support_route_accepts_smoke_without_restoring_turret(self):
        from tests.test_support_vehicle import resources as support_resources,spec
        r=support_resources();r.files.update({p:v for p,v in resources().files.items()
            if p==NATIVE or 'nbks.grenade' in p or 'nbk39/' in p or 'flash_grenade_launcher.def' in p})
        with tempfile.TemporaryDirectory() as tmp:
            result=compile_assembly(r,'receiver','receiver',{'support_vehicle':spec(),'smoke_launchers':[row(3,'body')]},tmp,'mw_supply_smoke')
            model=parse_model((Path(tmp)/'resource'/result['model_path']).read_bytes())
            self.assertNotIn('azimuth',model['bones']);self.assertIn('cw_smoke_0_root',model['bones'])

    def test_compatible_existing_smoke_keeps_extender_and_native_ammo(self):
        r=resources();p='entity/receiver/receiver.def';doc=parse(r.files[p]);root=doc.find('game_entity')
        root.append(block('{extender "smokescreen" {cooldown 7}{itemTags "grenade nbks"}}'))
        r.files[p]=doc.render().encode();p='entity/receiver/receiver.mdl';doc=parse(r.files[p]);doc.find('skeleton').append(block('{bone "smoke_shot1"}'));r.files[p]=doc.render().encode()
        result,root,_,_=self.compile(r,{'commander_only':True})
        ext=[n for n in root.find_all('extender') if n.args==['smokescreen']]
        self.assertEqual(len(ext),1);self.assertEqual(ext[0].find('cooldown').args,['7'])
        self.assertTrue(result['metadata']['smoke_launchers']['native_system_retained'])
        contract=result['metadata']['smoke_launchers']
        self.assertFalse(contract['replace_native_dispatch'])
        record=block('{"mw_smoke tank" {on "smoke_screen" {inherited}{spawn "nbk39" "smoke_shot1" x}}}')
        add_interactions(record,contract)
        handler=record.find('on')
        self.assertIsNotNone(handler.find('inherited'))
        self.assertTrue(any(n.args[1]=='smoke_shot1' for n in handler.walk('spawn')))

    def test_new_tubes_replace_dispatch_when_turret_removed_original_launchers(self):
        r=resources();p='entity/receiver/receiver.def';doc=parse(r.files[p]);root=doc.find('game_entity')
        root.append(block('{extender "smokescreen" {cooldown 7}{itemTags "grenade nbks"}}'))
        r.files[p]=doc.render().encode()
        p='entity/receiver/receiver.mdl';doc=parse(r.files[p])
        next(n for n in doc.walk('bone') if n.args==['azimuth']).append(block('{bone "smoke_shot1"}'))
        r.files[p]=doc.render().encode()
        result,root,model,_=self.compile(r,donor='donor')
        self.assertNotIn('smoke_shot1',model['bones'])
        contract=result['metadata']['smoke_launchers']
        self.assertTrue(contract['replace_native_dispatch'])
        self.assertFalse(contract['native_system_retained'])
        self.assertEqual(root.find_all('extender')[0].args,['smokescreen'])
        for own_handler in (False,True):
            with self.subTest(own_handler=own_handler):
                record=block('{"mw_smoke tank" {on "unrelated" {tags add "keep"}}}')
                if own_handler:record.append(block('{on "smoke_screen" {inherited}{spawn "nbk39" "smoke_shot1" x}}'))
                add_interactions(record,contract)
                add_interactions(record,contract)  # Re-registration replaces rather than duplicates dispatch.
                handler=next(n for n in record.find_all('on') if n.args[0]=='smoke_screen')
                self.assertEqual(handler.args,['smoke_screen','overload'])
                self.assertIsNone(handler.find('inherited'))
                shots=[n for n in handler.walk('spawn') if n.args[0]=='nbk39']
                self.assertEqual(len(shots),6)
                self.assertTrue(all(n.args[1] in model['bones'] for n in shots))
                self.assertIn('"keep"',record.render())

    def test_bad_input_or_unknown_native_system_fails_explicitly(self):
        for value in ({},[row(True)],[row(4)],[{**row(),'position':[0,float('nan'),0]}],[{**row(),'rotation':[False,0,0]}]):
            with self.subTest(value=value),self.assertRaises(SmokeError):configuration(value)
        r=resources();p='entity/receiver/receiver.def';r.files[p]=r.files[p].replace(b'{mass 19000}',b'{mass 19000}{extender "smokescreen" {itemTags "unknown"}}')
        with self.assertRaisesRegex(SmokeError,'munición incompatible'):self.compile(r,{'commander_only':True})
        r=resources();del r.files[NATIVE]
        with self.assertRaises(SmokeError):self.compile(r,{'commander_only':True})
        r=resources();del r.files['entity/nbk39/nbk39.mdl']
        with self.assertRaises(SmokeError):self.compile(r,{'commander_only':True})

    def test_default_recipe_does_not_change_assets_and_new_recipe_roundtrips(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=compile_assembly(resources(),'receiver','receiver',{'commander_only':True},Path(tmp)/'a','mw_same')
            b=compile_assembly(resources(),'receiver','receiver',{'commander_only':True,'smoke_launchers':[]},Path(tmp)/'b','mw_same')
            self.assertEqual(a['metadata']['file_hashes'],b['metadata']['file_hashes'])
            transform={'commander_only':True,'smoke_launchers':[row()]}
            a=compile_assembly(resources(),'receiver','receiver',transform,Path(tmp)/'c','mw_same')
            recipe=json.loads((Path(tmp)/'c/assembly_mw_same.json').read_text())
            b=compile_assembly(resources(),'receiver','receiver',recipe['metadata']['transform'],Path(tmp)/'d','mw_same')
            self.assertEqual(a['metadata']['file_hashes'],b['metadata']['file_hashes'])

    def test_campaign_initialization_adds_charges_once_and_preserves_spent_inventory(self):
        from tests.test_campaign import SCENE,make_save
        from workshop.campaign import load_campaign,plan_edit,apply_plan
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'copy.sav';make_save(path,SCENE);snapshot=load_campaign(path)
            for initialize in (True,False):
                profile={'weaponry_policy':'reset','smoke_launchers':{'initialize':initialize,'initial_item':'{item "nbks grenade" 3}'}}
                plan=plan_edit(snapshot,'0x9000','mw_smoke',2,state_profile=profile,keep_names=True)
                output=apply_plan(snapshot,plan)
                items=list(parse(output['campaign.scn']).walk('item'))
                self.assertEqual(any(n.args[0]=='nbks grenade' for n in items),initialize)
                self.assertTrue(any(n.args[0]=='old_ammo' for n in items))


if __name__=='__main__':unittest.main()
