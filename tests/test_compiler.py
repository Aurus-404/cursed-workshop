import tempfile
import struct
import unittest
from workshop.syntax import parse
from pathlib import Path
from workshop.compiler import (compile_assembly, CompileError, _animation_layout,
                               _retarget_animation, _quat_matrix, _allocate_crew_roles,
                               _rename_crew_and_places)
from workshop.geometry import parse_model, point, euler_matrix, multiply, translation
from tests.test_geometry import eply


class FakeResources:
    def __init__(self):
        self.files = {}
        self.files['properties/vehicle.ext'] = b'(define "holes" {volume "%volume%number" {able clear {hole}} {density 0}})'
        # Native cannon macros declare numbered workers, not invented aliases.
        self.files['properties/cannon_crew.ext'] = (
            b'(define "crew_wheeled_cannon_5" ("crew_open" place(gunner2) group(crew) bone(gunner2)))')
        for name, height, gun, armor in [('hull',10,'37mm',25),('donor',20,'20mm',8)]:
            folder='entity/'+name+'/'
            self.files[folder+name+'.def'] = ('{game_entity (include "'+name+'.ext") {Extension "'+name+'.mdl"} {mass 9000} {Chassis ("mobility" speed(40))} {Weaponry {place "gun" {weapon "'+gun+'" filling "ammo ap" 1}{gunner "gunner"}}} {extender "inventory" {box {item "ammo ap" 50}}}}').encode()
            self.files[folder+name+'.ext'] = ('{volume "turret" {thickness '+str(armor)+'}}').encode()
            self.files[folder+name+'.mdl'] = ('{skeleton {bone "basis" {bone "body" {VolumeView "body.ply"}{bone "driver"}{bone revolute "turret" {Position 0 0 '+str(height)+'}{VolumeView "turret.ply"}{bone "gun"}{bone "gunner"}}}}}{volume "body" {bone "body"}{box 1 1 1}}{volume "turret" {bone "turret"}{box 1 1 1}}').encode()
            self.files[folder+'body.ply'] = eply()
            self.files[folder+'turret.ply'] = eply()

    def read(self,path):
        return self.files[path]

    def resolve_template(self,name):
        folder='entity/'+name+'/'
        return {'id':name,'definition_path':folder+name+'.def','model_path':folder+name+'.mdl','extension_path':folder+name+'.ext'}


class CompilerTests(unittest.TestCase):
    def test_component_contracts_in_exported_sidecar_keep_saved_state(self):
        from workshop.campaign import _migrate_state, CampaignError
        from workshop.syntax import block
        for component in ('fuel', 'cooling_unit'):
            with self.subTest(component=component), tempfile.TemporaryDirectory() as temp:
                r = FakeResources()
                r.files['properties/vehicle.ext'] += b'(define "internal_part" {volume %0 {component %1}})'
                r.files['entity/hull/hull.ext'] += ('("internal_part" args "reservoir" "' + component + '")').encode()
                r.files['entity/hull/hull.mdl'] += b'{volume "reservoir" {bone "body"}{box 1 1 1}}'
                result = compile_assembly(r, 'hull', 'donor', {}, temp, 'mw_components')
                profile = result['metadata']['state_profile']
                self.assertIn(component, profile['allowed_damage_ids'])
                sidecar = Path(temp)/'resource/entity/-vehicle/workshop/mw_components/mw_components.ext'
                self.assertIn('"internal_part"', sidecar.read_text())
                state = block('{Entity "hull" {Component "' + component + '" {HealthBroken 29}}}')
                migrated = _migrate_state(state, block('{Entity "donor"}'), profile)
                self.assertIn('{HealthBroken 29}', migrated.render())
                with self.assertRaises(CampaignError):
                    _migrate_state(block('{Entity "hull" {Component "unknown_part"}}'), block('{Entity "donor"}'), profile)

    def _carriage(self):
        r = FakeResources()
        path = 'entity/hull/hull.def'
        root = parse(r.files[path]).find('game_entity')
        from workshop.syntax import block
        root.append(block('''{placer
          {place "driver" {group "crew"}{LinkBone "driver"}}
          {place "gunner" {group "crew"}{LinkBone "gunner"}}
          {place "charger" {group "crew"}{LinkBone "charger"}}
          {place "push_position" {group "crew"}{LinkBone "push_anchor"}}
          {place "tow_position" {group "crew"}{LinkBone "tow_anchor"}}}'''))
        root.append(block('''{boarder {door "emit"}
          {link "emit" "gunner"}{link "emit" "charger"}
          {link "push_position" "gunner"}{link "gunner" "push_position"}
          {link "tow_position" "push_position"}}'''))
        root.append(block('''{extender "cannon"
          {attack {workers "gunner" "charger"}}
          {move {workers "push_position" minWorkers 1}}
          {trailing {workers "tow_position"}}}'''))
        r.files[path] = root.render().encode()
        path = 'entity/hull/hull.mdl'
        model = parse_model(r.files[path])
        for name in ('emit', 'push_anchor', 'tow_anchor'):
            model['bones']['body']['node'].append(block('{bone "' + name + '"}'))
        model['bones']['turret']['node'].append(block('{bone "charger" {Position 1 2 3}}'))
        r.files[path] = model['document'].render().encode()
        return r

    def test_turret_swap_on_carriage_preserves_workers_and_native_seat_frames(self):
        from workshop.compiler import _validate_carriage_crew
        r = self._carriage()
        # A donor's optional internal seat is not another carriage worker.
        r.files['entity/donor/donor.mdl'] = r.files['entity/donor/donor.mdl'].replace(
            b'{bone "gunner"}', b'{bone "gunner"}{bone "commander"}')
        original = parse_model(r.files['entity/hull/hull.mdl'])
        with tempfile.TemporaryDirectory() as temp:
            result = compile_assembly(r, 'hull', 'donor', {}, temp, 'mw_carriage')
            folder = Path(temp)/'resource/entity/-vehicle/workshop/mw_carriage'
            root = parse((folder/'mw_carriage.def').read_bytes()).find('game_entity')
            model = parse_model((folder/'mw_carriage.mdl').read_bytes())
        self.assertEqual(result['metadata']['crew_roles'], ['charger', 'driver', 'gunner'])
        self.assertEqual(result['metadata']['crew_slots'], 3)
        self.assertIn('{link "tow_position" "push_position"}', root.render())
        self.assertIn('{link "gunner" "push_position"}', root.render())
        for role in ('gunner', 'charger'):
            seat = next(p for pl in root.find_all('placer') for p in pl.find_all('place') if p.args == [role])
            name = seat.find('LinkBone').args[0]
            self.assertTrue(name.startswith('cw_crew_'))
            self.assertEqual(model['bones'][name]['assets'], [])
            self.assertEqual(model['bones'][name]['world'], original['bones'][role]['world'])
        _validate_carriage_crew(root)

    def test_carriage_operators_work_a_turret_role_the_carriage_has_no_seat_for(self):
        # Lazarus: a Pz.IV turret on the 150 cm searchlight. Its "loader" has no
        # carriage seat; the build was refused (0.6.8 made it a seat nobody
        # could board). The carriage's own operator loads instead.
        r = self._carriage()
        r.files['entity/donor/donor.def'] = r.files['entity/donor/donor.def'].replace(
            b'{gunner "gunner"}', b'{gunner "gunner"}{charger "loader"}')
        # The carriage's own box is a small one, like the searchlight's {size 4 2}.
        r.files['entity/hull/hull.def'] = r.files['entity/hull/hull.def'].replace(
            b'{extender "inventory"', b'{extender "inventory_unused"').replace(
            b'{extender "cannon"', b'{extender "inventory" {box {size 2 1}}} {extender "cannon"')
        with tempfile.TemporaryDirectory() as temp:
            result = compile_assembly(r, 'hull', 'donor', {}, temp, 'mw_loader')
            root = parse((Path(temp)/'resource/entity/-vehicle/workshop/mw_loader/mw_loader.def').read_bytes()).find('game_entity')
        gun = next(p for w in root.find_all('Weaponry') for p in w.find_all('place') if p.args == ['gun'])
        self.assertEqual(gun.find('charger').args, ['charger'], "the carriage's second operator loads")
        self.assertNotIn('loader', result['metadata']['crew_roles'])
        box = next(e for e in root.find_all('extender') if e.args == ['inventory']).find('box')
        self.assertIsNone(box.find('size'), "the module's stowage is not squeezed into the carriage's small box")
        self.assertIn('{item "ammo ap" 50}', box.render())

    def test_carriage_missing_worker_transition_is_rejected_before_export(self):
        r = self._carriage()
        r.files['entity/hull/hull.def'] = r.files['entity/hull/hull.def'].replace(b'{link "push_position" "gunner"}', b'')
        r.files['entity/hull/hull.def'] = r.files['entity/hull/hull.def'].replace(b'{link "gunner" "push_position"}', b'')
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(CompileError, 'enlace nativo'):
                compile_assembly(r, 'hull', 'donor', {}, temp, 'mw_incomplete')
            self.assertEqual(list(Path(temp).rglob('*.def')), [])

    def test_complete_carriage_edit_does_not_count_alternate_places_as_humans(self):
        with tempfile.TemporaryDirectory() as temp:
            result = compile_assembly(self._carriage(), 'hull', 'hull', {'vehicle_edit': True}, temp, 'mw_edit')
        self.assertEqual(result['metadata']['crew_slots'], 3)

    def test_volume_remapping_preserves_macro_and_field_names_but_maps_argument_values(self):
        from workshop.compiler import _rename_volume
        from workshop.syntax import block
        source=block('("shield" args "shield" 4.5)')
        mapped=_rename_volume(source,{'shield':'module_shield'})
        self.assertEqual(mapped.head,'shield')
        self.assertEqual(mapped.args,['args','module_shield','4.5'])
        self.assertEqual(source.args,['args','shield','4.5'])
        named=_rename_volume(block('("armor" volume(shield) settings({volume "shield"}) nested(("shield" args "shield")))'),
                             {'armor':'wrong_macro','volume':'wrong_field','shield':'module_shield'})
        self.assertEqual(named.head,'armor')
        self.assertIn('volume(module_shield)',named.render())
        self.assertIn('{volume "module_shield"}',named.render())
        self.assertIn('("shield" args "module_shield")',named.render())
        self.assertNotIn('wrong_',named.render())
        definition=_rename_volume(block('(define "shield" {volume "shield"})'),{'shield':'module_shield'})
        self.assertEqual(definition.args,['shield'])
        self.assertIn('{volume "module_shield"}',definition.render())

    def test_shield_macro_survives_a_collision_between_chassis_and_donor_volume_names(self):
        r=FakeResources()
        r.files['properties/vehicle.ext']+=b'(define "shield" {volume "%0" {thickness %1}})'
        for name,owner in [('hull','body'),('donor','turret')]:
            path=f'entity/{name}/{name}.mdl'
            r.files[path]+=('{volume "shield" {bone "'+owner+'"}{box 1 1 1}}').encode()
        r.files['entity/donor/donor.ext']+=b'("shield" args "shield" 4.5)'
        with tempfile.TemporaryDirectory() as temp:
            result=compile_assembly(r,'hull','donor',{},temp,'mw_shield')
            folder=Path(temp)/'resource/entity/-vehicle/workshop/mw_shield'
            nodes=[n for path in folder.iterdir() if path.suffix in ('.def','.ext') for n in parse(path.read_bytes()).walk()]
        calls=[n for n in nodes if n.opener=='(' and n.head=='shield']
        self.assertTrue(any(n.args==['args','module_shield','4.5'] for n in calls))
        self.assertFalse(any(n.opener=='(' and n.head=='module_shield' for n in nodes))

    def test_crew_order_recognizes_wrapped_unquoted_passenger_macros(self):
        from workshop.compiler import _prioritize_crew
        r=FakeResources()
        r.files['properties/tank_crew.ext']=b'''
          (define "crew_human" {placer {place "%place" {group "%group"}}})
          (define "seat_human" ("crew_human" place(%place) group(passenger)))
          (define "deck_row" ("seat_human" place(deck)))'''
        root=parse('''{game_entity {extension "vehicle.mdl"} ("deck_row")
          {placer {place "driver" {group "crew"}{LinkBone "driver"}}}
          {placer {place "gunner" {group "crew"}{LinkBone "gunner"}}}
          {boarder {door "emit"}{link "emit" "deck"}}}''').find('game_entity')
        _prioritize_crew(r,root);text=root.render()
        self.assertLess(text.index('place "driver"'),text.index('("deck_row")'))
        self.assertLess(text.index('place "gunner"'),text.index('("deck_row")'))
        self.assertIn('{link "emit" "deck"}',text)
        _prioritize_crew(r,root)
        self.assertEqual([(p.args[0],p.find('group').args[0]) for pl in root.find_all('placer') for p in pl.find_all('place')],
                         [('driver','crew'),('gunner','crew')])

    def test_mixed_and_added_passengers_follow_all_crew_without_losing_seats(self):
        from workshop.compiler import _prioritize_crew
        r=FakeResources()
        root=parse('''{game_entity {extension "v.mdl"}
          {placer {place "rider" {group "passenger"}{LinkBone "deck"}}
                  {place "driver" {group "crew"}{LinkBone "driver"}}}
          {placer {place "loader" {group "crew"}{LinkBone "loader"}}}
          {placer {place "extra" {group "passenger"}{LinkBone "extra_seat"}}}
          {boarder {door "emit"}{link "emit" "rider"}{link "emit" "driver"}{link "emit" "loader"}}}''').find('game_entity')
        before={p.args[0]:p.render() for pl in root.find_all('placer') for p in pl.find_all('place')}
        _prioritize_crew(r,root)
        places=[p for pl in root.find_all('placer') for p in pl.find_all('place')]
        self.assertEqual([p.args[0] for p in places],['driver','loader','rider','extra'])
        self.assertEqual({p.args[0]:p.render() for p in places},before)
        self.assertEqual([n.args[1] for b in root.find_all('boarder') for n in b.find_all('link')],['driver','loader','rider'])
        self.assertEqual(sum(len(b.find_all('door')) for b in root.find_all('boarder')),1)

    def test_export_rejects_valueless_linkbone_in_all_resource_blocks(self):
        from workshop.compiler import _validate_export
        for suffix in ('def', 'ext', 'mdl'):
            with self.subTest(suffix=suffix), self.assertRaisesRegex(CompileError, 'String expected'):
                _validate_export({'vehicle.'+suffix:b'{skeleton {bone "body"}}{placer {place "gunner" {LinkBone}}}'})
        # Empty strings and macro declarations retain their native contracts.
        _validate_export({'vehicle.def':b'{placer {place "gunner" {LinkBone ""}}}'})
        _validate_export({'crew.ext':b'(define "seat" {placer {place "%place" {LinkBone "%bone"}}})'})

    def test_invalid_source_linkbone_cannot_reach_export_files(self):
        resources = FakeResources()
        key = 'entity/hull/hull.def'
        resources.files[key] = resources.files[key].replace(
            b'{mass 9000}', b'{placer {place "driver" {group "crew"}{LinkBone}}}{mass 9000}')
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)/'compiled'
            with self.assertRaisesRegex(CompileError, 'String expected'):
                compile_assembly(resources,'hull','donor',{},destination,'mw_invalid_link')
            self.assertFalse(list(destination.rglob('*.def')))

    def test_replacing_a_structural_root_is_rejected_before_any_export(self):
        resources = FakeResources()
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)/'compiled'
            with self.assertRaisesRegex(CompileError, 'chasis completo'):
                compile_assembly(resources, 'hull', 'donor', {'recipient_mount':'basis'}, destination, 'mw_invalid_root')
            self.assertFalse(destination.exists())

    def test_global_cannon_operator_is_materialized_before_role_allocation(self):
        resources=FakeResources()
        self._truck(resources,b'{placer {place "gunner" {group "crew"}}}'
                    b'{Weaponry {place "mgun1" {type "hull"}{weapon "mg"}{gunner "gunner"}{charger "gunner"}}}')
        resources.files['properties/cannon.ext']=b'{props "with_gun"}{Weaponry {place "gun" {gunner "gunner"}}}'
        key='entity/donor/donor.def'
        resources.files[key]=resources.files[key].replace(b'{game_entity',b'{game_entity (include "/properties/cannon.ext")').replace(b'{gunner "gunner"}',b'')
        resources.files[key]=resources.files[key].replace(b'{mass 9000}',b'{placer {place "gunner" {group "crew"}}}{mass 9000}')
        with tempfile.TemporaryDirectory() as temp:
            result=compile_assembly(resources,'truck','donor',{'recipient_mount':'@free'},temp,'vww_defaults')
            root=parse((Path(temp)/'resource/entity/-vehicle/workshop/vww_defaults/vww_defaults.def').read_bytes()).find('game_entity')
            places={p.args[0]:p for p in root.find('Weaponry').find_all('place')}
            self.assertEqual(places['gun'].find('gunner').args,['gunner'])
            self.assertEqual(places['mgun1'].find('gunner').args,['gunner2'])
            self.assertIn('with_gun',[v for p in root.find_all('props') for v in p.args])
            self.assertIn('main_gunner_to_module',[d['code'] for d in result['diagnostics']])

    def test_complete_edit_recognizes_generic_crew_and_nested_crew_wrappers(self):
        for macro in ('crew', 'crew_driver'):
            with self.subTest(macro=macro), tempfile.TemporaryDirectory() as temp:
                resources = FakeResources()
                resources.files['properties/custom_crew.ext'] = b'''
                  (define "crew_human" {placer {place "%place" {group "%group"}{LinkBone "%bone"}}})
                  (define "crew" ("crew_human" place(%place) group(crew) bone(%bone)))
                  (define "crew_driver" ("crew" place(%place) bone(%bone)))'''
                resources.files['entity/hull/hull.ext'] += (
                    '(include "/properties/custom_crew.ext") ("'+macro+'" place(driver) bone(driver))'
                    '("crew_human" place(seat1) group(passenger) bone(driver))').encode()
                result = compile_assembly(resources, 'hull', 'hull', {'commander_only': True}, temp, 'mw_crew')
                self.assertEqual(result['metadata']['crew_roles'], ['driver'])
                self.assertEqual(result['metadata']['crew_slots'], 1)
                root = parse((Path(temp)/'resource'/result['definition_path']).read_bytes()).find('game_entity')
                places = [p for pl in root.find_all('placer') for p in pl.find_all('place')]
                self.assertEqual([(p.args[0], p.find('group').args[0]) for p in places],
                                 [('driver', 'crew'), ('seat1', 'passenger')])

    def test_generic_crew_call_is_retained_from_shared_includes(self):
        from workshop.compiler import _inherited_crew
        resources = FakeResources()
        resources.files['properties/shared.ext'] = b'("crew" place(driver) bone(driver))'
        root = parse('{game_entity (include "/properties/shared.ext")}').find('game_entity')
        self.assertEqual([n.head for n in _inherited_crew(resources, root)], ['crew'])

    def test_explicit_operator_overrides_global_property_and_keeps_its_seat(self):
        resources=FakeResources()
        resources.files['properties/defaults.ext']=b'{Weaponry {place "gun" {gunner "commander"}{charger "charger"}}}'
        resources.files['entity/donor/donor.def']=resources.files['entity/donor/donor.def'].replace(b'{game_entity',b'{game_entity (include "/properties/defaults.ext")')
        with tempfile.TemporaryDirectory() as temp:
            compile_assembly(resources,'hull','donor',{},temp,'vww_override')
            root=parse((Path(temp)/'resource/entity/-vehicle/workshop/vww_override/vww_override.def').read_bytes()).find('game_entity')
            weapon=root.find('Weaponry').find('place')
            self.assertEqual(weapon.find('gunner').args,['gunner'])
            self.assertEqual(weapon.find('charger').args,['charger'])

    def test_carried_aiming_ancestor_keeps_axes_limits_and_exact_rest_pose_without_carriage(self):
        resources=FakeResources()
        key='entity/donor/donor.mdl'
        resources.files[key]=resources.files[key].replace(b'bone "basis"',b'bone revolute "basis" {Orientation 1 0 0 0 -1 0 0 0 1}')
        resources.files['entity/donor/donor.def']=resources.files['entity/donor/donor.def'].replace(b'{mass 9000}',b'{bone "basis" {speed2 15}}{bone "turret" {limits -10 10}{speed2 20}}{mass 9000}')
        resolve=resources.resolve_template
        resources.resolve_template=lambda name:{**resolve(name),'kind':'artillery'} if name=='donor' else resolve(name)
        original=parse_model(resources.files[key])
        transform={'auto_seat':False,'turret':{'position':[3,4,5],'rotation':[0,0,35]}}
        with tempfile.TemporaryDirectory() as temp:
            result=compile_assembly(resources,'hull','donor',transform,temp,'vww_chain')
            folder=Path(temp)/'resource/entity/-vehicle/workshop/vww_chain'
            final=parse_model((folder/'vww_chain.mdl').read_bytes())
            mapping=result['metadata']['bone_mapping']
            outer=mapping['basis']
            self.assertEqual(final['bones'][mapping['turret']]['parent'],outer)
            self.assertEqual(final['bones'][outer]['node'].args[0],'revolute')
            self.assertEqual(final['bones'][outer]['assets'],[])
            self.assertEqual(sum(bool(b['assets']) for b in final['bones'].values()),2)
            adjustment=multiply(multiply(translation([0,0,10]),euler_matrix([0,0,35],[3,4,5])),translation([0,0,-20]))
            for name in ('turret','gun'):
                expected=multiply(adjustment,original['bones'][name]['world'])
                actual=final['bones'][mapping[name]]['world']
                for row in range(4):
                    for col in range(4):self.assertAlmostEqual(actual[row][col],expected[row][col],places=5)
            root=parse((folder/'vww_chain.def').read_bytes()).find('game_entity')
            drives={n.args[0]:n for n in root.find_all('bone')}
            self.assertEqual(drives[outer].find('speed2').args,['15'])
            self.assertEqual(drives[mapping['turret']].find('limits').args,['-10','10'])

    def test_transplant_weapon_armor_chassis_and_exact_world_transform(self):
        resources=FakeResources()
        transform={'hull':{'position':[4,2,1],'rotation':[0,0,30]},'turret':{'position':[1,-2,3],'rotation':[0,0,45]},'mount':[0,0,10],'auto_seat':False}
        with tempfile.TemporaryDirectory() as temp:
            result=compile_assembly(resources,'hull','donor',transform,temp,'vww_test')
            root=Path(temp)/'resource/entity/-vehicle/workshop/vww_test'
            definition=(root/'vww_test.def').read_text()
            # The engine resolves {bone}/Weaponry against the model loaded so far:
            # the model and its armor must come first (game crash otherwise).
            entity=parse(definition).find('game_entity').children
            heads=[node.head.casefold() for node in entity]
            self.assertEqual(heads.count('extension'),1)
            self.assertLess(heads.index('extension'),heads.index('weaponry'))
            self.assertEqual(entity[heads.index('extension')+1].args,['vww_test.ext'])
            self.assertTrue(all(i>heads.index('extension') for i,h in enumerate(heads) if h=='bone'))
            armor=(root/'vww_test.ext').read_text()
            model=parse_model((root/'vww_test.mdl').read_text())
            self.assertIn('"20mm"',definition)
            self.assertNotIn('"37mm"',definition)
            self.assertIn('speed(40)',definition)
            self.assertIn('{thickness 8}',armor)
            self.assertNotIn('{thickness 25}',armor)
            expected=[1,-2,13]
            actual=point(model['bones']['turret']['world'],[0,0,0])
            for left,right in zip(actual,expected):
                self.assertAlmostEqual(left,right,places=6)
            self.assertEqual(len(model['bones']),6)
            self.assertTrue(result['ready_for_campaign'])
            self.assertFalse(result['runtime_verified'])

    def test_does_not_overwrite_output_or_accept_path_in_id(self):
        with tempfile.TemporaryDirectory() as temp:
            Path(temp,'unrelated').write_text('preserve')
            with self.assertRaisesRegex(CompileError,'preserved'):
                compile_assembly(FakeResources(),'hull','donor',{},temp,'vww_test')
            with self.assertRaises(CompileError):
                compile_assembly(FakeResources(),'hull','donor',{},temp,'../bad')
            self.assertEqual(Path(temp,'unrelated').read_text(),'preserve')

    def test_unanchored_weapon_follows_the_main_turret_unless_it_is_a_hull_weapon(self):
        # Katyusha "gun" has no bone references: its launcher turns with "turret",
        # so it travels with a module holding that turret. A {type "hull"} place
        # without references is the vehicle's own and stays (no weapon to move).
        resources=FakeResources()
        key='entity/donor/donor.def'
        resources.files[key]=resources.files[key].replace(b'place "gun"',b'place "special"')
        with tempfile.TemporaryDirectory() as temp:
            result=compile_assembly(resources,'hull','donor',{},temp,'vww_rockets')
            places=[p.args[0] for p in parse((Path(temp)/'resource/entity/-vehicle/workshop/vww_rockets/vww_rockets.def').read_text()).find('game_entity').find('Weaponry').find_all('place')]
            self.assertIn('special',places)
        resources.files[key]=resources.files[key].replace(b'place "special"',b'place "special" {type "hull"}')
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(CompileError,'no resolved weapon place'):
                compile_assembly(resources,'hull','donor',{},temp,'vww_test')
            self.assertEqual(list(Path(temp).iterdir()),[])

    def test_volume_collision_keeps_both_parts_and_maps_damage(self):
        resources=FakeResources()
        for name,owner in [('hull','body'),('donor','turret')]:
            key='entity/'+name+'/'+name+'.mdl'
            resources.files[key] += ('{volume "hole1" {bone "'+owner+'"}{box 1 1 1}}').encode()
        with tempfile.TemporaryDirectory() as temp:
            result=compile_assembly(resources,'hull','donor',{},temp,'vww_collision')
            model=parse_model((Path(temp)/'resource/entity/-vehicle/workshop/vww_collision/vww_collision.mdl').read_bytes())
            self.assertIn('hole1',model['volumes'])
            self.assertIn('module_hole1',model['volumes'])
            self.assertEqual(model['volumes']['hole1']['bone'],'body')
            self.assertEqual(model['volumes']['module_hole1']['bone'],'turret')
            self.assertEqual(result['metadata']['state_profile']['donor_name_map']['hole1'],'module_hole1')

    def test_declared_vehicle_lifecycle_requires_real_sources(self):
        resources=FakeResources()
        resources.files['entity/hull/hull.def']=resources.files['entity/hull/hull.def'].replace(b'{game_entity',b'{game_entity (include "/properties/tank.ext")')
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(CompileError,'required destruction'):
                compile_assembly(resources,'hull','donor',{},temp,'vww_lifecycle')
            self.assertEqual(list(Path(temp).iterdir()),[])

    def test_sparse_animation_full_rotation_translation_and_unknown_bone_filter(self):
        names=['turret','old_hatch']
        raw=b'EANM'+struct.pack('<HH',1,6)+b'FRMS'+struct.pack('<I',10)+b'BMAP'+struct.pack('<I',2)
        raw+=b''.join(struct.pack('<I',len(name))+name.encode() for name in names)
        raw+=b'FRM2'+struct.pack('<HB',0,2)+struct.pack('<BH6f',0,11,0,0,0,0,0,0)+struct.pack('<BH3f',1,2,0,0,0)
        raw+=b'FRM2'+struct.pack('<HB',9,1)+struct.pack('<BH6f',0,3,0,0,0,0,0,0)
        correction=euler_matrix([30,40,50],[1,2,3])
        moved=_retarget_animation(raw,{'turret':correction})
        emitted,frames=_animation_layout(moved)
        self.assertEqual(emitted,['turret'])
        self.assertEqual([frame['index'] for frame in frames],[0,9])
        for frame in frames:
            name,position,rotation,flags,deformation=frame['tracks'][0]
            self.assertEqual(struct.unpack_from('<3f',moved,position),(1,2,3))
            quaternion_matrix=_quat_matrix(struct.unpack_from('<3f',moved,rotation))
            for row in range(3):
                for column in range(3):
                    self.assertAlmostEqual(quaternion_matrix[row][column],correction[row][column],places=6)

    def test_legacy_matrix_animation_drops_renames_and_translates(self):
        # Header 0000 0400 clips (French D2): FRMN / BONE / MATR (3x4) / VISI chunks.
        names=['turret','hatch']
        raw=b'EANM'+struct.pack('<HH',0,4)+b'FRMS'+struct.pack('<I',3)+b'BMAP'+struct.pack('<I',2)
        raw+=b''.join(struct.pack('<I',len(name))+name.encode() for name in names)
        matrix=struct.pack('<12f',0,0,-1, 0,1,0, 1,0,0, 5,6,7)
        raw+=b'FRMN'+struct.pack('<I',0)+b'BONE'+struct.pack('<I',0)+b'MATR'+matrix+b'VISI'+struct.pack('<I',1)
        raw+=b'BONE'+struct.pack('<I',1)+b'MATR'+matrix
        raw+=b'FRMN'+struct.pack('<I',2)+b'BONE'+struct.pack('<I',1)+b'VISI'+struct.pack('<I',0)
        identity=euler_matrix()
        self.assertEqual(_retarget_animation(raw,{'turret':identity,'hatch':identity}),raw)
        moved=_retarget_animation(raw,{'turret':euler_matrix([0,0,0],[1,2,3])},{'turret':'module_turret'})
        emitted,frames=_animation_layout(moved)
        self.assertEqual(emitted,['module_turret'])
        self.assertEqual([frame['index'] for frame in frames],[0])  # frame 2 only animated the hatch
        name,translation_at,_,_,_=frames[0]['tracks'][0]
        self.assertEqual(struct.unpack_from('<3f',moved,translation_at),(6,8,10))
        self.assertIn(b'VISI'+struct.pack('<I',1),moved)
        self.assertEqual(struct.unpack_from('<9f',moved,translation_at-36),(0,0,-1,0,1,0,1,0,0))
        dropped=set()  # a rotated bone keeps its rest pose in a legacy clip
        rotated=_retarget_animation(raw,{'turret':euler_matrix([0,0,30])},None,dropped)
        self.assertEqual(dropped,{'turret'})
        self.assertNotIn(b'MATR',rotated)
        self.assertIn(b'VISI'+struct.pack('<I',1),rotated)

    def test_opaque_mesh_animation_passes_only_without_any_changed_binding(self):
        names=['trackl','turret']
        blob=b'opaque native deformation payload'
        raw=b'EANM'+struct.pack('<HH',1,6)+b'FRMS'+struct.pack('<I',2)+b'BMAP'+struct.pack('<I',2)
        raw+=b''.join(struct.pack('<I',len(name))+name.encode() for name in names)
        raw+=b'FRM2'+struct.pack('<HB',0,2)+struct.pack('<BH6f',0,43,1,2,3,0,0,0)+struct.pack('<I',len(blob))+blob
        raw+=struct.pack('<BH6f',1,15,0,0,0,0,0,0)
        identity={'trackl':euler_matrix(),'turret':euler_matrix()}
        self.assertEqual(_retarget_animation(raw,identity),raw)
        dropped=set()  # a deformation cannot follow a moved bone: only that track goes
        moved=_retarget_animation(raw,{**identity,'trackl':euler_matrix(position=[1,0,0])},None,dropped)
        self.assertEqual(dropped,{'trackl'})
        self.assertEqual([track[0] for track in _animation_layout(moved)[1][0]['tracks']],['turret'])
        # A removed turret bone drops its track; the hull's deformation block survives intact.
        kept=_retarget_animation(raw,{'trackl':euler_matrix()})
        emitted,frames=_animation_layout(kept)
        self.assertEqual(emitted,['trackl'])
        name,position,rotation,flags,deformation=frames[0]['tracks'][0]
        self.assertEqual((flags,kept[deformation[0]+4:deformation[0]+deformation[1]]),(43,blob))
        self.assertEqual(struct.unpack_from('<3f',kept,position),(1,2,3))

    def test_obsolete_module_crew_seat_removed_when_donor_has_no_loader(self):
        resources=FakeResources()
        key='entity/hull/hull.def'
        resources.files[key]=resources.files[key].replace(b'{gunner "gunner"}',b'{gunner "gunner"}{charger "charger"}')
        resources.files[key]=resources.files[key].replace(b'{mass 9000}',b'{placer {place "driver" {group "crew"}}{place "gunner" {group "crew"}}{place "charger" {group "crew"}}}{mass 9000}')
        key='entity/hull/hull.mdl'
        resources.files[key]=resources.files[key].replace(b'{bone "driver"}',b'{bone "driver"}{bone "charger"}')
        with tempfile.TemporaryDirectory() as temp:
            result=compile_assembly(resources,'hull','donor',{},temp,'vww_crew')
            root=Path(temp)/'resource/entity/-vehicle/workshop/vww_crew'
            model=parse_model((root/'vww_crew.mdl').read_bytes())
            self.assertNotIn('charger',model['bones'])
            self.assertNotIn('place "charger"',(root/'vww_crew.def').read_text())
            self.assertEqual(result['metadata']['crew_slots'],2)

    def _truck(self, resources, extra_def=b''):
        folder='entity/truck/'
        resources.files[folder+'truck.def']=(b'{game_entity (include "truck.ext") {Extension "truck.mdl"} {mass 3000} {Chassis ("mobility" speed(60))}'
                                             b'{placer {place "driver" {group "crew"}}}'+extra_def+b'}')
        resources.files[folder+'truck.ext']=b'{volume "body" {thickness 5}}'
        resources.files[folder+'truck.mdl']=b'{skeleton {bone "basis" {bone "body" {VolumeView "body.ply"}{bone "driver"}}}}{volume "body" {bone "body"}{box 1 1 1}}'
        resources.files[folder+'body.ply']=eply()
        return 'truck'

    def test_free_mount_puts_a_weapon_on_a_chassis_without_turret(self):
        resources=FakeResources()
        self._truck(resources)
        with tempfile.TemporaryDirectory() as temp:
            result=compile_assembly(resources,'truck','donor',{},temp,'vww_free')
            root=Path(temp)/'resource/entity/-vehicle/workshop/vww_free'
            model=parse_model((root/'vww_free.mdl').read_bytes())
            self.assertEqual(model['bones']['turret']['parent'],'body')  # hangs from the chassis structure
            self.assertIn('driver',model['bones'])                      # the chassis keeps everything
            definition=(root/'vww_free.def').read_text()
            self.assertIn('"20mm"',definition)
            self.assertIn('speed(60)',definition)
            self.assertEqual(result['metadata']['recipient_mount'],'@free')

    def test_free_mount_renames_weapon_places_and_crew_the_chassis_already_has(self):
        resources=FakeResources()
        # A chassis that keeps its own "gun" operated by its own "gunner".
        self._truck(resources,b'{placer {place "gunner" {group "crew"}}}{Weaponry {place "gun" {weapon "7mm"}{gunner "gunner"}}}')
        key='entity/donor/donor.def'
        resources.files[key]=resources.files[key].replace(b'{mass 9000}',b'{placer {place "gunner" {group "crew"}}}{mass 9000}')
        with tempfile.TemporaryDirectory() as temp:
            compile_assembly(resources,'truck','donor',{'recipient_mount':'@free'},temp,'vww_both')
            definition=(Path(temp)/'resource/entity/-vehicle/workshop/vww_both/vww_both.def').read_text()
            places=[p.args for p in parse(definition).find('game_entity').find('Weaponry').find_all('place')]
            self.assertIn(['gun'],places)
            self.assertIn(['gun2'],places)  # numbered like native places, never invented
            gun=next(p for p in parse(definition).find('game_entity').find('Weaponry').find_all('place') if p.args==['gun2'])
            self.assertEqual(gun.find('LinkBone').args,['module_gun'])  # donor barrel cannot become the kept gun slot's implicit host
            model=parse_model((Path(temp)/'resource/entity/-vehicle/workshop/vww_both/vww_both.mdl').read_bytes())
            self.assertNotIn('gun',model['bones'])
            self.assertIn('module_gun',model['bones'])
            self.assertIsNotNone(gun.find('basic'),'added implicit primary must be selectable alongside the original primary')
            self.assertIsNotNone(gun.find('ai_in_manual'),'independent auxiliary retains native AI control')
            original=next(p for p in parse(definition).find('game_entity').find('Weaponry').find_all('place') if p.args==['gun'])
            self.assertIsNone(original.find('basic'),'native chassis primary is unchanged')
            self.assertIsNone(original.find('ai_in_manual'),'native chassis AI contract is unchanged')
            self.assertIsNone(gun.find('type'),'selectability never turns a cannon/flamethrower into a machine gun')
            self.assertIn('{gunner "gunner2"}',definition)
            self.assertIn('{place "gunner"',definition)
            self.assertIn('{place "gunner2"',definition)

    def test_free_mounted_gun_keeps_its_own_implicit_sight(self):
        # T-29 + BA-6 on the deck: the BA-6 "gun" place names no sight and aims
        # through the model's "foresight1". Added as "gun2" it aimed through the
        # chassis gun's sight: key 7 moved the main gun and the view stuck.
        resources=FakeResources()
        self._truck(resources,b'{placer {place "gunner" {group "crew"}}}{Weaponry {place "gun" {weapon "7mm"}{gunner "gunner"}{foresight "foresight1"}}}')
        key='entity/truck/truck.mdl'
        resources.files[key]=resources.files[key].replace(b'{bone "driver"}',b'{bone "driver"}{bone "foresight1"}')
        key='entity/donor/donor.mdl'
        resources.files[key]=resources.files[key].replace(b'{bone "gun"}',b'{bone "gun" {bone "foresight1"}}')
        with tempfile.TemporaryDirectory() as temp:
            compile_assembly(resources,'truck','donor',{'recipient_mount':'@free'},temp,'vww_sight')
            definition=(Path(temp)/'resource/entity/-vehicle/workshop/vww_sight/vww_sight.def').read_text()
            places={p.args[0]:p for p in parse(definition).find('game_entity').find('Weaponry').find_all('place')}
            model=parse_model((Path(temp)/'resource/entity/-vehicle/workshop/vww_sight/vww_sight.mdl').read_bytes())
            sight=places['gun2'].find('foresight').args[0]
            self.assertNotEqual(sight,'foresight1','the added gun must not aim through the chassis gun sight')
            self.assertIn(sight,model['bones'])
            self.assertEqual(model['bones'][sight]['parent'],places['gun2'].find('LinkBone').args[0])
            self.assertEqual(places['gun'].find('foresight').args,['foresight1'],'the chassis gun keeps its sight')

    def test_an_added_turret_fires_its_own_numbered_clip_and_leaves_the_chassis_clips(self):
        # TheOppsAreOntoMe: a T30 turret added to a KV-1 took over "fire" and
        # "open": its 20 mm recoiled when the 76 mm fired and its hatch opened
        # with the commander's. The engine plays "fireN" for "gunN".
        def clip(bone):
            raw=b'EANM'+struct.pack('<HH',1,6)+b'FRMS'+struct.pack('<I',2)+b'BMAP'+struct.pack('<I',1)
            raw+=struct.pack('<I',len(bone))+bone.encode()
            return raw+b'FRM2'+struct.pack('<HB',0,1)+struct.pack('<BH3f',0,2,0,0,0)
        resources=FakeResources()
        self._truck(resources,b'{placer {place "gunner" {group "crew"}}}{Weaponry {place "gun" {weapon "7mm"}{gunner "gunner"}}}')
        key='entity/truck/truck.mdl'
        resources.files[key]=resources.files[key].replace(b'{skeleton {bone "basis"',b'{skeleton {animation {sequence "fire"} {sequence "open"}} {bone "basis"')
        resources.files['entity/truck/fire.anm']=clip('body');resources.files['entity/truck/open.anm']=clip('driver')
        key='entity/donor/donor.mdl'
        resources.files[key]=resources.files[key].replace(b'{skeleton',b'{skeleton {animation {sequence "fire"} {sequence "open"}}',1)
        resources.files['entity/donor/fire.anm']=clip('gun');resources.files['entity/donor/open.anm']=clip('turret')
        with tempfile.TemporaryDirectory() as temp:
            compile_assembly(resources,'truck','donor',{'recipient_mount':'@free'},temp,'vww_clips')
            folder=Path(temp)/'resource/entity/-vehicle/workshop/vww_clips'
            animation=parse_model((folder/'vww_clips.mdl').read_bytes())['skeleton'].find('animation')
            files={s.args[0]:s.find('file').args[0] for s in animation.find_all('sequence')}
            self.assertEqual(sorted(files),['fire','fire2','open'])
            tracks=lambda name:_animation_layout((folder/files[name]).read_bytes())[0]
            self.assertEqual(tracks('fire'),['body'],'the chassis gun keeps its own recoil')
            self.assertEqual(tracks('open'),['driver'],'the chassis hatches keep their own clip')
            self.assertEqual(tracks('fire2'),['module_gun'],'the added gun2 recoils with its own clip')

    def test_towing_hitch_and_demolition_charge_use_the_games_own_contracts(self):
        # ChuckWagon (winch) and frenchies (Goliath charge): a point plus native data.
        resources=FakeResources()
        self._truck(resources,b'{props "tractor" "tractor_light" "wheeled"}')
        for path in ('set/stuff/explosive/goliath.weapon','set/stuff/explosive/goliath.60kg.ammo','set/stuff/explosive/goliath.100kg.ammo'):
            resources.files[path]=b'{weapon_bullet}'
        transform={'commander_only':True,
                   'towing':{'tier':'heavy','parent':'body','position':[-30,0,-5],'rotation':[0,0,180]},
                   'charge':{'charge':'60kg','parent':'body','position':[20,0,5],'rotation':[0,0,0]}}
        with tempfile.TemporaryDirectory() as temp:
            result=compile_assembly(resources,'truck','truck',transform,temp,'vww_points')
            folder=Path(temp)/'resource/entity/-vehicle/workshop/vww_points'
            root=parse((folder/'vww_points.def').read_bytes()).find('game_entity')
            model=parse_model((folder/'vww_points.mdl').read_bytes())
        props=[v for n in root.find_all('props') for v in n.args]
        self.assertEqual([v for v in props if v.startswith('tractor')],['tractor','tractor_heavy'],'one tier, the chosen one')
        self.assertIn('wheeled',props)
        self.assertEqual(model['bones']['link2']['parent'],'body')
        self.assertAlmostEqual(model['bones']['link2']['world'][0][0],-1,places=5,msg='the hook faces backwards')
        place=next(p for p in root.find('Weaponry').find_all('place') if p.args==['cw_explosive'])
        self.assertEqual(place.find('weapon').args,['weapon goliath','filling','ammo goliath 60kg','1'])
        self.assertEqual(place.find('gunner').args,['driver'])
        self.assertEqual(place.find('foresight').args,['cw_charge'])
        self.assertEqual(result['metadata']['towing']['tier'],'heavy')
        bad=dict(transform,towing={'tier':'huge','parent':'body'})
        with tempfile.TemporaryDirectory() as temp,self.assertRaises(ValueError):
            compile_assembly(resources,'truck','truck',bad,temp,'vww_bad')

    def test_a_bone_animation_without_file_names_its_source_clip(self):
        # Lazarus, MACE triple Polsten: {Sequence "fire00"} inside a bone plays
        # fire00.anm from the model's folder; in the workshop folder it was
        # missing and the game stopped loading the campaign.
        resources=FakeResources()
        self._truck(resources)
        key='entity/donor/donor.mdl'
        resources.files[key]=resources.files[key].replace(b'{bone "gun"}',b'{bone "gun" {animation {sequence "fire00" {speed 0.7}}}}')
        raw=b'EANM'+struct.pack('<HH',1,6)+b'FRMS'+struct.pack('<I',2)+b'BMAP'+struct.pack('<I',1)+struct.pack('<I',3)+b'gun'
        resources.files['entity/donor/fire00.anm']=raw+b'FRM2'+struct.pack('<HB',0,1)+struct.pack('<BH3f',0,2,0,0,0)
        with tempfile.TemporaryDirectory() as temp:
            compile_assembly(resources,'truck','donor',{'recipient_mount':'@free'},temp,'vww_bone_clip')
            folder=Path(temp)/'resource/entity/-vehicle/workshop/vww_bone_clip'
            sequence=next(parse((folder/'vww_bone_clip.mdl').read_bytes()).walk('sequence'))
            self.assertEqual(sequence.args,['fire00'])
            self.assertIsNotNone(sequence.find('file'),'an implicit clip would be looked up in the workshop folder')
            self.assertTrue((folder/sequence.find('file').args[0]).is_file())

    def test_imported_implicit_body_ids_and_armor_do_not_configure_chassis_body(self):
        resources = FakeResources()
        self._truck(resources, b'{volume "body" {thickness 5}}')
        resources.files['properties/vehicle.ext'] += b'(define "body" {volume "body" {thickness %thickness}})'
        resources.files['entity/donor/donor.ext'] += b'("body" thickness(9))'
        resources.files['entity/donor/donor.mdl'] = resources.files['entity/donor/donor.mdl'].replace(
            b'{bone revolute "turret"', b'{bone revolute "turret" {parameters "id=body;native=keep;"}')
        with tempfile.TemporaryDirectory() as temp:
            compile_assembly(resources, 'truck', 'donor', {'recipient_mount': '@free'}, temp, 'vww_implicit')
            folder = Path(temp) / 'resource/entity/-vehicle/workshop/vww_implicit'
            model = parse_model((folder / 'vww_implicit.mdl').read_bytes())
            parameters = model['bones']['turret']['node'].find('parameters').args
            self.assertEqual(parameters, ['id=module_body;native=keep;'])
            root = parse((folder / 'vww_implicit.def').read_text()).find('game_entity')
            nodes = [n for path in folder.iterdir() if path.suffix in ('.def', '.ext')
                     for n in parse(path.read_bytes()).walk()]
            volumes = {v.args[0]: v for v in nodes if v.head.casefold() == 'volume'}
            self.assertEqual(volumes['body'].find('thickness').args, ['5'])
            self.assertEqual(volumes['module_body'].find('thickness').args, ['9'])
            self.assertFalse(any(n.opener == '(' and n.head == 'body' for n in nodes))

    def test_a_seat_named_like_a_volume_stays_after_the_crew(self):
        # Potato Emperor: Cromwell "seat_human ... animation(armor1)" went into the
        # armour EXT, ahead of the crew, and shop crews boarded on the deck.
        r = FakeResources()
        key = 'entity/hull/hull.def'
        r.files[key] = r.files[key].replace(b'{Weaponry', b'("seat_human" place(seat1) bone(body) animation(body)){Weaponry', 1)
        with tempfile.TemporaryDirectory() as temp:
            compile_assembly(r, 'hull', 'donor', {}, temp, 'mw_seat')
            folder = Path(temp)/'resource/entity/-vehicle/workshop/mw_seat'
            self.assertNotIn('seat_human', (folder/'mw_seat.ext').read_text())
            self.assertIn('seat_human', (folder/'mw_seat.def').read_text())

    def test_turret_swap_keeps_a_second_turret_weapon_its_own_crew_and_ammo(self):
        # T-26 M1931 + Flak 30: the right turret's DT stays. It must not share
        # the module gun's "gunner", and its drums share the generic "ammo"
        # token with the replaced gun's rounds but are still needed.
        resources=FakeResources()
        key='entity/hull/hull.def'
        resources.files[key]=(resources.files[key]
            .replace(b'{Weaponry {place "gun" {weapon "37mm" filling "ammo ap" 1}{gunner "gunner"}}}',
                     b'{placer {place "driver" {group "crew"}}{place "gunner" {group "crew"}}{place "commander" {group "crew"}}}'
                     b'{Weaponry {place "gun" {weapon "37mm" filling "ammo ap" 1}{gunner "commander"}}'
                     b'{place "mgun3" {type "mgun_turret_right"}{weapon "dt" filling "ammo mgun dt" 63}{gunner "gunner"}{LinkBone "body"}}}')
            .replace(b'{item "ammo ap" 50}',b'{item "ammo ap" 50}{item "ammo mgun dt" 600}'))
        with tempfile.TemporaryDirectory() as temp:
            compile_assembly(resources,'hull','donor',{},temp,'vww_twin')
            definition=(Path(temp)/'resource/entity/-vehicle/workshop/vww_twin/vww_twin.def').read_text()
            weaponry=parse(definition).find('game_entity').find('Weaponry')
            places={p.args[0]:p for p in weaponry.find_all('place')}
            self.assertEqual(places['mgun3'].find('gunner').args,['gunner2'])
            self.assertEqual(places['gun'].find('gunner').args,['gunner'])
            self.assertIn('{place "gunner2"',definition)
            self.assertIn('{item "ammo mgun dt" 600}',definition)

    def test_recipe_records_the_tool_version_that_built_it(self):
        import json
        from workshop import __version__
        with tempfile.TemporaryDirectory() as temp:
            result=compile_assembly(FakeResources(),'hull','donor',{},temp,'vww_version')
            recipe=json.loads((Path(temp)/'assembly_vww_version.json').read_text(encoding='utf-8'))
        self.assertEqual(result['tool_version'],__version__)
        self.assertEqual(recipe['tool_version'],__version__)

    def test_new_turret_gun_comes_before_a_kept_hull_gun(self):
        # James Rommel, 21/10: Pz.Kpfw. B2 + Pz III turret. Direct control only
        # offers the first untyped place; after the kept hull gun "gun1" the
        # turret gun fired for the AI but the player could never pick it.
        resources=FakeResources()
        key='entity/hull/hull.def'
        resources.files[key]=resources.files[key].replace(
            b'{Weaponry {place "gun" {weapon "37mm" filling "ammo ap" 1}{gunner "gunner"}}}',
            b'{placer {place "driver" {group "crew"}}{place "gunner" {group "crew"}}}'
            b'{Weaponry {place "gun1" {weapon "75mm" filling "ammo he" 1}{gunner "driver"}{LinkBone "body"}}'
            b'{place "gun" {weapon "37mm" filling "ammo ap" 1}{gunner "gunner"}}}')
        with tempfile.TemporaryDirectory() as temp:
            compile_assembly(resources,'hull','donor',{},temp,'vww_b2')
            definition=(Path(temp)/'resource/entity/-vehicle/workshop/vww_b2/vww_b2.def').read_text()
            places=[p.args[0] for p in parse(definition).find('game_entity').find('Weaponry').find_all('place')]
            self.assertEqual(places[0],'gun')
            self.assertIn('gun1',places)

    def test_free_mount_names_renamed_gunner_of_implicit_gunner_weapon(self):
        # T-20 + Pak 36: the Pak has no {gunner}; the "gunner" place is the chassis' DT gunner.
        resources=FakeResources()
        self._truck(resources,b'{placer {place "gunner" {group "crew"}}}{Weaponry {place "mgun" {weapon "7mm"}{gunner "gunner"}}}')
        key='entity/donor/donor.def'
        resources.files[key]=(resources.files[key].replace(b'{gunner "gunner"}',b'')
                              .replace(b'{mass 9000}',b'{placer {place "gunner" {group "crew"}}}{mass 9000}'))
        with tempfile.TemporaryDirectory() as temp:
            compile_assembly(resources,'truck','donor',{'recipient_mount':'@free'},temp,'vww_implicit')
            definition=(Path(temp)/'resource/entity/-vehicle/workshop/vww_implicit/vww_implicit.def').read_text()
            places={p.args[0]:p for p in parse(definition).find('game_entity').find('Weaponry').find_all('place')}
            self.assertEqual(places['gun'].find('gunner').args,['gunner2'])
            self.assertEqual(places['mgun'].find('gunner').args,['gunner'])

    def test_module_main_gun_keeps_gunner_when_chassis_gunner_only_has_hull_mg(self):
        # T-20 (hull DT, "gunner") + Pak: direct control and weapon keys follow
        # "gunner", so the Pak keeps it and the DT gunner becomes "gunner2".
        resources=FakeResources()
        self._truck(resources,b'{placer {place "gunner" {group "crew"}}}'
                    b'{Weaponry {place "mgun1" {type "hull"}{weapon "7mm"}{gunner "gunner"}{charger "gunner"}}}')
        key='entity/donor/donor.def'
        resources.files[key]=(resources.files[key].replace(b'{gunner "gunner"}',b'')
                              .replace(b'{mass 9000}',b'{placer {place "gunner" {group "crew"}}}{mass 9000}'))
        with tempfile.TemporaryDirectory() as temp:
            result=compile_assembly(resources,'truck','donor',{'recipient_mount':'@free'},temp,'vww_main')
            root=parse((Path(temp)/'resource/entity/-vehicle/workshop/vww_main/vww_main.def').read_text()).find('game_entity')
            places={p.args[0]:p for p in root.find('Weaponry').find_all('place')}
            self.assertEqual(places['mgun1'].find('gunner').args,['gunner2'])
            self.assertEqual(places['mgun1'].find('charger').args,['gunner2'])
            self.assertTrue(places['gun'].find('gunner') is None or places['gun'].find('gunner').args==['gunner'])
            crew={p.args[0] for pl in root.find_all('placer') for p in pl.find_all('place')}
            self.assertEqual(crew,{'driver','gunner','gunner2'})
            self.assertIn('main_gunner_to_module',[d['code'] for d in result['diagnostics']])

    def test_chassis_main_gun_keeps_gunner(self):
        # The chassis' own main gun stays with "gunner"; the added weapon is renamed.
        resources=FakeResources()
        self._truck(resources,b'{placer {place "gunner" {group "crew"}}}{Weaponry {place "gun" {weapon "7mm"}{gunner "gunner"}}}')
        key='entity/donor/donor.def'
        resources.files[key]=resources.files[key].replace(b'{mass 9000}',b'{placer {place "gunner" {group "crew"}}}{mass 9000}')
        with tempfile.TemporaryDirectory() as temp:
            result=compile_assembly(resources,'truck','donor',{'recipient_mount':'@free'},temp,'vww_keep')
            self.assertNotIn('main_gunner_to_module',[d['code'] for d in result['diagnostics']])

    def test_gun_crew_gets_a_dismount_clip_and_can_be_left_behind(self):
        resources=FakeResources()
        self._truck(resources)
        key='entity/donor/donor.def'
        resources.files[key]=(resources.files[key].replace(b'{gunner "gunner"}',b'{charger "charger charger2"}')
            .replace(b'{mass 9000}',
                     b'{placer {place "gunner" {group "crew"}}{place "charger" {group "crew"}}{place "charger2" {group "crew"}}}'
                     b'{boarder {anm "gunner" {forward {end "cannon_small_gunner" 10}}}'
                     b'{anm "charger" {forward {end "cannon_small_charger" 10}}}'
                     b'{anm "charger2" {forward {end "cannon_small_charger2" 10}}}'
                     b'{link "emit3" "charger2" {anm "charger2"}}}{mass 9000}'))
        resolve=resources.resolve_template
        resources.resolve_template=lambda name: {**resolve(name),'props':['cannon_small']} if name=='donor' else resolve(name)
        with tempfile.TemporaryDirectory() as temp:
            result=compile_assembly(resources,'truck','donor',{'recipient_mount':'@free','crew_exclude':['charger2']},temp,'vww_crew')
            root=parse((Path(temp)/'resource/entity/-vehicle/workshop/vww_crew/vww_crew.def').read_text()).find('game_entity')
            anms={a.args[0]:a for b in root.find_all('boarder') for a in b.find_all('anm') if a.args}
            self.assertIn('emit_tank_1_hold',anms['gunner'].find('reverse').render())   # can get off a burning vehicle
            self.assertNotIn('charger2',anms)
            crew={p.args[0] for pl in root.find_all('placer') for p in pl.find_all('place')}
            self.assertNotIn('charger2',crew)
            self.assertEqual(root.find('Weaponry').find('place').find('charger').args,['charger'])
            self.assertFalse([l for l in root.walk('link') if 'charger2' in l.args])
            self.assertNotIn('charger2',result['metadata']['crew_roles'])
            with self.assertRaisesRegex(CompileError,'aims'):
                compile_assembly(resources,'truck','donor',{'recipient_mount':'@free','crew_exclude':['gunner']},temp+'/b','vww_crew2')

    def test_hand_placed_static_seat_is_used_as_given(self):
        from workshop.compiler import _static_crew
        mounted=parse('{bone "turret" {bone "gunner"} {bone "charger"}}').children[0]
        detached,report=_static_crew(mounted,{'charger'},{'turret'},lambda name: translation([-60,9,30]),
                                     euler_matrix(),((-50,-20),(50,20)),overrides={'charger':{'position':[5,-3,12],'yaw':None}})
        self.assertEqual(report[0]['to'],[5,-3,12])
        self.assertTrue(report[0]['manual'])
        # Automatic placement keeps a body's width inside a narrow hull.
        mounted=parse('{bone "turret" {bone "charger"}}').children[0]
        _,report=_static_crew(mounted,{'charger'},{'turret'},lambda name: translation([0,40,30]),
                              euler_matrix(),((-50,-15),(50,15)))
        self.assertEqual(report[0]['to'][1],9.0)

    def test_free_mount_role_collision_keeps_pose_and_both_bone_bindings(self):
        resources=FakeResources()
        self._truck(resources,b'{placer {place "gunner" {LinkBone "gunner"}{group "crew"}}}'
                    b'{Weaponry {place "mgun" {weapon "7mm"}{gunner "gunner"}}}'
                    b'{boarder {anm "gunner" {forward {end "chassis_gunner_pose"}}}}')
        resources.files['entity/truck/truck.mdl']=resources.files['entity/truck/truck.mdl'].replace(
            b'{bone "driver"}',b'{bone "driver"}{bone "gunner"}')
        key='entity/donor/donor.def'
        resources.files[key]=(resources.files[key].replace(b'{gunner "gunner"}',b'')
            .replace(b'{mass 9000}',
                     b'{placer {place "gunner" {LinkBone "gunner"}{group "crew"}}}'
                     b'{boarder {anm "gunner" {forward {end "cannon_small_gunner" 10}}}'
                     b'{door "emit1"}{link "emit1" "gunner" {anm "gunner"}}}{mass 9000}'))
        with tempfile.TemporaryDirectory() as temp:
            result=compile_assembly(resources,'truck','donor',{'recipient_mount':'@free'},temp,'vww_pose')
            folder=Path(temp)/'resource/entity/-vehicle/workshop/vww_pose'
            definition=parse((folder/'vww_pose.def').read_bytes()).find('game_entity')
            boarders=definition.find_all('boarder')
            poses={anm.args[0]: anm for boarder in boarders for anm in boarder.find_all('anm')}
            self.assertEqual(poses['gunner'].find('forward').find('end').args,['chassis_gunner_pose'])
            self.assertEqual(poses['gunner2'].find('forward').find('end').args,['cannon_small_gunner','10'])
            link=next(link for boarder in boarders for link in boarder.find_all('link') if link.args[:1]==['emit1'])
            self.assertEqual(link.args,['emit1','gunner2'])
            self.assertEqual(link.find('anm').args,['gunner2'])
            places={place.args[0]:place for placer in definition.find_all('placer') for place in placer.find_all('place')}
            model=parse_model((folder/'vww_pose.mdl').read_bytes())
            self.assertEqual(places['gunner'].find('LinkBone').args,['gunner'])
            self.assertEqual(places['gunner2'].find('LinkBone').args,['module_gunner'])
            self.assertIn('module_gunner',model['bones'])
            self.assertEqual(result['metadata']['crew_roles'],['driver','gunner','gunner2'])
            self.assertNotIn('gunner_mw',definition.render())

    def test_role_allocator_reserves_donor_workers_and_never_invents_names(self):
        resources=FakeResources()
        # gunner2 is already used by another donor worker: take a real free commander.
        self.assertEqual(_allocate_crew_roles(resources,{'gunner','gunner2'},{'gunner','driver'}),{'gunner':'commander'})
        # Never use a passenger, driver or an unverified gunner_mw as a fallback.
        with self.assertRaisesRegex(CompileError,'No unused native crew role'):
            _allocate_crew_roles(resources,{'gunner'},{'gunner','gunner2','commander','charger'})

    def test_role_rename_updates_cannon_workers_but_preserves_animation_resources(self):
        root=parse('{game_entity {placer {place "gunner" {group "crew"}}}'
                   '{boarder {anm "gunner" {forward {end "cannon_small_gunner"}}}'
                   '{link "gunner" "commander" {anm "gunner"}}}'
                   '{extender "cannon" {attack {workers "gunner" "charger" "commander"}}'
                   '{animation {aim "cannon_small_gunner_aim" "cannon_small_charger_aim" "cannon_small_commander_aim"}}}}').find('game_entity')
        _rename_crew_and_places(root,{'gunner':'gunner2'}, {})
        self.assertEqual(root.find('placer').find('place').args,['gunner2'])
        self.assertEqual(root.find('boarder').find('link').args,['gunner2','commander'])
        cannon=root.find('extender')
        self.assertEqual(cannon.find('attack').find('workers').args,['gunner2','charger','commander'])
        self.assertEqual(cannon.find('animation').find('aim').args,
                         ['cannon_small_gunner_aim','cannon_small_charger_aim','cannon_small_commander_aim'])

    def test_carried_gun_loaders_leave_the_turning_part_and_stay_on_the_chassis(self):
        from workshop.compiler import _static_crew, _rotating_bones, _aiming_roles
        root=parse('{game_entity {bone "turret" {limits -30 30}} {bone "gun_rot" {speed2 10}}'
                   '{Weaponry {place "gun" {charger "charger charger2"}}}}').find('game_entity')
        self.assertEqual(_rotating_bones(root),{'turret','gun_rot'})
        self.assertEqual(_aiming_roles(root.find('Weaponry').find_all('place')),{'gunner'})  # implicit gunner
        mounted=parse('{bone "turret" {bone "gunner"} {bone "charger"} {bone "charger2"} {bone "gun_rot"}}').children[0]
        worlds={'charger':translation([-60,9,30]),'charger2':translation([-60,7,30])}
        detached,report=_static_crew(mounted,{'charger','charger2'},{'turret'},worlds.__getitem__,
                                     euler_matrix(),((-50,-20),(50,20)))
        self.assertEqual(sorted(child.args[0] for child in mounted.find_all('bone')),['gun_rot','gunner'])  # aimer stays
        self.assertEqual([node.args[0] for node in detached],['charger','charger2'])
        first,second=(item['to'] for item in report)
        self.assertEqual(first,[-44.0,9,30])                    # inside the footprint (6% margin)
        self.assertGreaterEqual(abs(second[0]-first[0]),10)     # not on top of the first loader

    def test_volume_binding_already_broken_in_the_source_is_kept(self):
        # lorraine_37l_xx binds volume "gun" to "gun1" (its bone is "gun1_xx");
        # the game tolerates it, so only bindings the tool breaks are rejected.
        from workshop.compiler import _validate_export
        files={'a.mdl':b'{skeleton {bone "body"}} {volume "gun" {bone "gun1"}}'}
        with self.assertRaisesRegex(CompileError,'binds missing bone'):
            _validate_export(files)
        _validate_export(files,source_missing_bones=frozenset({'gun1'}))

    def test_seat_markers_expand_nested_passenger_macros(self):
        from workshop.compiler import seat_markers
        resources=FakeResources()
        self._truck(resources,b'(define "passenger_human" {placer {place "%place" {group "passenger"} {linkbone "%bone"}}})'
                              b'(define "passenger_2_human" ("passenger_human" place(seat1) bone(seat01)) ("passenger_human" place(seat2) bone(seat02)))'
                              b'("passenger_2_human")')
        seats={seat['place']:seat for seat in seat_markers(resources,'truck')}
        self.assertEqual(seats['driver']['group'],'crew')
        self.assertEqual((seats['seat2']['group'],seats['seat2']['bone']),('passenger','seat02'))
        seats={seat['place']:seat for seat in seat_markers(resources,'donor')}
        self.assertTrue(seats.get('gunner',{'weapon':True})['weapon'])

    def test_a_towed_guns_own_poses_win_and_its_push_places_are_not_extra_crew(self):
        from workshop.compiler import seat_markers
        resources=FakeResources()
        # Shared towed-gun crew (like properties/cannon_mgun.ext): the same two men
        # aim it (gunner, commander) or push it (driver1, driver2).
        resources.files['properties/towed.ext']=(b'{placer {place "gunner" {group "crew"} {linkbone "gunner"}}'
            b' {place "commander" {group "crew"} {linkbone "commander"}}'
            b' {place "driver1" {group "crew"} {linkbone "driver1"}} {place "driver2" {group "crew"} {linkbone "driver2"}}}'
            b'{boarder {anm "gunner" {forward {end "pose_tripod"}}} {anm "driver1" {forward {end "push"}}}'
            b' {link "driver1" "gunner"} {link "driver2" "commander"}}')
        key='entity/donor/donor.def'
        resources.files[key]=resources.files[key].replace(b'{game_entity',
            b'{game_entity (include "/properties/towed.ext") {props "cannon"} {boarder {anm "gunner" {forward {end "own_seat"}}}}')
        plain=resources.resolve_template
        resources.resolve_template=lambda name:{**plain(name),**({'props':['cannon']} if name=='donor' else {})}
        seats={seat['place']:seat for seat in seat_markers(resources,'donor')}
        self.assertEqual(set(seats)&{'driver1','driver2'},set())
        self.assertEqual(seats['gunner']['pose'],'own_seat')
        with tempfile.TemporaryDirectory() as temp:
            result=compile_assembly(resources,'hull','donor',{},temp,'mw_towed')
            text=(Path(temp)/'resource'/result['definition_path']).read_text(encoding='utf-8')
        self.assertIn('own_seat',text)
        self.assertNotIn('pose_tripod',text)
        # Saved campaigns keep crew in the push places: their links must stay.
        self.assertIn('{link "driver1" "gunner"}',' '.join(text.split()))

    def test_a_roof_mg_keeps_the_deck_rider_who_mans_it(self):
        # Wolverine/Sherman .50: ("gunner_open" place(gunner2) ...) declares a
        # passenger who climbs on and fires it. On a Churchill the MG came
        # without that seat and nobody could use it.
        resources=FakeResources()
        resources.files['properties/tank_crew.ext']=(b'(define "gunner_open" {sensor {visor "top" {bone "%visorbone"}}}'
            b'{placer {place "%place" {group "%group"} {linkbone "%bone"} {visor "top"}}}'
            b'{boarder {door "%door"} {door "%door2"} {link "%door" "%place" {anm "%anm_left"}} {link "%door2" "%place" {anm "%anm_right"}}})')
        hull,donor='entity/hull/hull.def','entity/donor/donor.def'
        resources.files['entity/hull/hull.mdl']=resources.files['entity/hull/hull.mdl'].replace(b'{bone "driver"}',b'{bone "driver"}{bone "emit1"}')
        resources.files[hull]=resources.files[hull].replace(b'{mass 9000}',
            b'{placer {place "driver" {group "crew"}}}{boarder {door "emit1"} {anm "left" {forward {end "seat_hatch"}}} {link "emit1" "driver" {anm "left"}}}{mass 9000}')
        resources.files['entity/donor/donor.mdl']=resources.files['entity/donor/donor.mdl'].replace(b'{bone "gunner"}',b'{bone "gunner"}{bone "mgun"}{bone "rider"}')
        resources.files[donor]=resources.files[donor].replace(b'{gunner "gunner"}}',b'{gunner "gunner"}}{place "mgun" {type "aa"}{weapon "mg"}{gunner "rider"}}')
        resources.files[donor]=resources.files[donor].replace(b'{mass 9000}',
            b'{placer {place "gunner" {group "crew"}}}{boarder {anm "left" {forward {end "stand_at_mg"}}}}'
            b'("gunner_open" place(rider) bone(rider) visorbone(mgun) door(emit9) door2(emit1) anm_left(left) anm_right(left) group(passenger)){mass 9000}')
        with tempfile.TemporaryDirectory() as temp:
            result=compile_assembly(resources,'hull','donor',{},temp,'mw_rider')
            text=' '.join((Path(temp)/'resource'/result['definition_path']).read_text(encoding='utf-8').split())
        self.assertIn('{place "rider" {group "passenger"} {linkbone "rider"} {visor "top"}}',text)
        # The donor hull's door emit9 is not on the chassis: the rider boards through the chassis' emit1.
        self.assertIn('{link "emit1" "rider" {anm "mw_left"}}',text)
        self.assertNotIn('emit9',text)
        # Its own clip, standing at the MG, not the chassis crew's hatch seat.
        self.assertIn('{anm "mw_left" {forward {end "stand_at_mg"}}}',text)
        self.assertIn('{link "emit1" "driver" {anm "left"}}',text)
        self.assertIn('rider_door_rebased',[d['code'] for d in result['diagnostics']])

    def test_a_chassis_seat_on_the_replaced_turret_moves_to_the_new_mount(self):
        # Vidarr's Stuart + searchlight: the Stuart's deck rider stood on its
        # turret (("gunner_open" bone(gunner2))); with the turret gone the place
        # pointed at a missing bone and the game crashed spawning the vehicle.
        resources=FakeResources()
        resources.files['properties/tank_crew.ext']=b'(define "gunner_open" {placer {place "%place" {group "passenger"} {linkbone "%bone"}}})'
        hull='entity/hull/hull.def'
        resources.files['entity/hull/hull.mdl']=resources.files['entity/hull/hull.mdl'].replace(b'{bone "gunner"}',b'{bone "gunner"}{bone "rider"}',1)
        resources.files[hull]=resources.files[hull].replace(b'{mass 9000}',b'("gunner_open" place(gunner2) bone(rider) group(passenger)){mass 9000}')
        with tempfile.TemporaryDirectory() as temp:
            result=compile_assembly(resources,'hull','donor',{},temp,'mw_lost')
            text=' '.join((Path(temp)/'resource'/result['definition_path']).read_text(encoding='utf-8').split())
            model=parse_model((Path(temp)/'resource'/result['model_path']).read_bytes())
        self.assertNotIn('rider',model['bones'])
        mount=result['metadata']['bone_mapping'].get(result['metadata']['donor_mount'],result['metadata']['donor_mount'])
        self.assertIn('{place "gunner2" {group "passenger"} {linkbone "%s"}}'%mount,text)
        self.assertIn(mount,model['bones'])
        self.assertIn('seat_rebased',[d['code'] for d in result['diagnostics']])

    def test_an_inventory_slot_whose_place_left_with_the_turret_goes_and_its_gun_stays(self):
        from workshop.compiler import _drop_orphan_slots
        root=parse('{game_entity {extender "inventory" {weapon "mgun" {mask "mountable"}} {weapon "mgun_cmd" {mask "mountable"}}'
                   ' {box {item "browning" filled {user "mgun"}} {item "mg34" filled {user "mgun_cmd"}}}}'
                   ' {Weaponry {place "gun"} {place "mgun_cmd"}}}').find('game_entity')
        diagnostics=[]
        self.assertEqual(_drop_orphan_slots(root,{'gun','mgun'},diagnostics),['mgun'])
        text=' '.join(root.render().split())
        self.assertNotIn('{weapon "mgun" {mask',text)
        self.assertIn('{item "browning" filled }',text)
        self.assertIn('{user "mgun_cmd"}',text)  # a slot the chassis never had is not the tool's to judge
        self.assertEqual([d['code'] for d in diagnostics],['orphan_slot_removed'])

    def _passenger_resources(self):
        from unittest.mock import patch
        resources=FakeResources()
        resources.files['properties/tank_crew.ext']=(b'(define "seat_human" {placer {place "%place" {group "passenger"} {LinkBone "%bone"}}}'
            b'{boarder {door "%door"} {link "%door" "%place" {anm "%animation"} {forward putoff} {reverse puton}}})')
        hull='entity/hull/hull.def'
        resources.files['entity/hull/hull.mdl']=resources.files['entity/hull/hull.mdl'].replace(b'{bone "driver"}',b'{bone "driver"}{bone "emit1"}{bone "seat01" {Position 0 -5 2}}')
        resources.files[hull]=resources.files[hull].replace(b'{mass 9000}',
            b'{placer {place "driver" {group "crew"}}}{boarder {door "emit1"} {anm "armor" {forward {end "pose_seat_armor_1"}}} {link "emit1" "driver"}}'
            b'("seat_human" place(seat1) bone(seat01) door(emit1) animation(armor)){mass 9000}')
        poses=[{'id':'pose_seat_armor_1','source':'x','begin':'board_HighArmor','reverse':'emit_tank_1'},
               {'id':'pose_squat_empty','source':'y','begin':'board_lowarmor','reverse':'emit_tank_1'}]
        item=patch('workshop.passenger_seats.pose_catalog',return_value=poses);item.start();self.addCleanup(item.stop)
        return resources

    def test_passenger_seats_are_added_anywhere_with_no_cap(self):
        resources=self._passenger_resources()
        added=[{'pose':'pose_seat_armor_1','parent':'body','position':[i*4,0,6],'rotation':[0,0,0]} for i in range(20)]
        with tempfile.TemporaryDirectory() as temp:
            result=compile_assembly(resources,'hull','donor',{'passenger_seats':{'added':added}},temp,'mw_riders20')
            out=Path(temp)/'resource'
            text=' '.join((out/result['definition_path']).read_text(encoding='utf-8').split())
            model=parse_model((out/result['model_path']).read_bytes())
        seats=result['metadata']['passenger_seats']['added']
        self.assertEqual([s['place'] for s in seats],[f'mw_seat{i}' for i in range(1,21)])
        self.assertEqual(model['bones']['mw_seat20_pos']['parent'],'body')
        self.assertEqual([round(v,3) for v in point(model['bones']['mw_seat3_pos']['world'],[0,0,0])][:1],[8.0])
        self.assertIn('{place "mw_seat1" {group "passenger"} {LinkBone "mw_seat1_pos"}}',text)
        self.assertIn('{anm "mw_seat1" {forward {begin "board_HighArmor"} {end "pose_seat_armor_1"}} {reverse {end "emit_tank_1"}}}',text)
        self.assertIn('{link "emit1" "mw_seat1" {anm "mw_seat1"} {forward putoff} {reverse puton}}',text)

    def test_a_vehicles_own_seat_takes_another_pose_and_spot(self):
        resources=self._passenger_resources()
        change={'native':{'seat1':{'pose':'pose_squat_empty','offset':[0,0,3]}}}
        with tempfile.TemporaryDirectory() as temp:
            result=compile_assembly(resources,'hull','donor',{'passenger_seats':change},temp,'mw_native')
            out=Path(temp)/'resource'
            text=' '.join((out/result['definition_path']).read_text(encoding='utf-8').split())
            model=parse_model((out/result['model_path']).read_bytes())
        self.assertIn('{link "emit1" "seat1" {anm "mw_pose_seat1"}',text)
        self.assertIn('{anm "mw_pose_seat1" {forward {begin "board_lowarmor"} {end "pose_squat_empty"}}',text)
        self.assertEqual([round(v,3) for v in point(model['bones']['seat01']['world'],[0,0,0])],[0.0,-5.0,5.0])
        with tempfile.TemporaryDirectory() as temp,self.assertRaisesRegex(CompileError,'pose'):
            compile_assembly(resources,'hull','donor',{'passenger_seats':{'native':{'seat1':{'pose':'nope'}}}},temp,'mw_bad')
        with tempfile.TemporaryDirectory() as temp,self.assertRaisesRegex(CompileError,'asiento seatX'):
            compile_assembly(resources,'hull','donor',{'passenger_seats':{'native':{'seatX':{'offset':[1,0,0]}}}},temp,'mw_bad')

    def _rider_resources(self):
        from workshop.geometry import inverse
        resources=FakeResources()
        hull='entity/hull/hull.def'
        resources.files['entity/hull/hull.mdl']=resources.files['entity/hull/hull.mdl'].replace(b'{bone "driver"}',b'{bone "driver"}{bone "emit1"}')
        resources.files[hull]=resources.files[hull].replace(b'{mass 9000}',
            b'{placer {place "driver" {group "crew"}}}{boarder {door "emit1"} {link "emit1" "driver"}}{mass 9000}')
        resources.files['entity/mount/mount.mdl']=(b'{skeleton {bone "basis" {bone revolute "yaw" {Position 0 0 5} {limits -20 20}'
            b'{bone revolute "pitch" {bone "mgun"}} {bone "seat" {Position 0 -10 0}}} {bone "stand" {Position 5 0 0}}}}')
        bones=parse_model(resources.files['entity/mount/mount.mdl'])['bones']
        def support(id,seat,turns):
            return {'id':id,'label':id,'template':'mount','model_path':'entity/mount/mount.mdl','root':'yaw','yaw':'yaw','pitch':'pitch',
                    'link':'mgun','seat':seat,'pose':'pose_ring','turns':turns,'yaw_limits':[-20,20],'pitch_limits':[-10,40],
                    'seat_frame':multiply(inverse(bones['yaw']['world']),bones[seat]['world']),'bones':['mgun','pitch','seat','yaw']}
        catalog={'supports':[support('ring','seat',True),support('fixed','stand',False)],
                 'poses':[{'id':'pose_ring','label':'pose_ring','source':'x','begin':'board_HighArmor','reverse':'emit_tank_1'}]}
        weapons=[{'id':'mg42_belt','label':'mg42','entity':'mg42','ammo_item':'ammo mgun_ger belt','ammo_rounds':300,'tags':['mgun']}]
        from unittest.mock import patch
        patches=[patch('workshop.rider_mg.catalog',return_value=catalog),patch('workshop.rider_mg.weapon_catalog',return_value=weapons),
                 patch('workshop.commander_mg._stock',return_value=resources)]
        for item in patches:item.start();self.addCleanup(item.stop)
        return resources

    def test_passenger_mgs_add_a_rider_seat_gun_slot_and_boarding_on_any_bone(self):
        resources=self._rider_resources()
        rows=[{'support':'ring','pose':'pose_ring','item':'mg42_belt','parent':'turret','position':[0,0,10],'rotation':[0,0,90],
               'yaw':[-90,90],'pitch':[-5,50],'seat':[0,2,0]},
              {'support':'fixed','pose':'pose_ring','item':'mg42_belt','parent':'body','position':[0,-20,8],'rotation':[0,0,0],
               'yaw':[-30,30],'pitch':[-10,40]}]
        with tempfile.TemporaryDirectory() as temp:
            result=compile_assembly(resources,'hull','donor',{'rider_mgs':rows},temp,'mw_riders')
            out=Path(temp)/'resource'
            text=' '.join((out/result['definition_path']).read_text(encoding='utf-8').split())
            model=parse_model((out/result['model_path']).read_bytes())
        mounts=result['metadata']['rider_mgs']['mounts']
        self.assertEqual([m['place'] for m in mounts],['mw_rider1','mw_rider2'])
        self.assertEqual(result['metadata']['state_profile']['rider_mgs'],result['metadata']['rider_mgs'])
        # The first operator hangs from the turning bone; the second keeps his own spot.
        self.assertEqual(model['bones']['rider1_seat']['parent'],'rider1_yaw')
        self.assertEqual(model['bones'][mounts[1]['bones']['@seat']]['parent'],'body')
        self.assertEqual(model['bones']['rider1_yaw']['parent'],'turret')
        self.assertIn('{limits -90.0 90.0}',' '.join(model['document'].render().split()))
        self.assertIn('{place "mw_rider1" {group "passenger"} {linkbone "rider1_seat"} {visor "mw_rider1_top"}}',text)
        self.assertIn('{link "emit1" "mw_rider1" {anm "mw_rider1"} {forward putoff} {reverse puton}}',text)
        self.assertIn('{anm "mw_rider1" {forward {begin "board_HighArmor"} {end "pose_ring"}} {reverse {end "emit_tank_1"}}}',text)
        self.assertIn('{place "mgun_rider1" {type "aa"} {gunner "mw_rider1"} {charger "mw_rider1"} {LinkBone "rider1_mgun"}',text)
        self.assertIn('{weapon "mgun_rider2" {mask "mountable"}}',text)
        self.assertIn('{item "mg42_belt" filled {user "mgun_rider2"}}',text)
        self.assertIn('(include "/properties/abm.inc")',text)
        self.assertIn('mgun_rider1',result['metadata']['state_profile']['weapon_places'])

    def test_passenger_mgs_have_no_count_cap(self):
        resources=self._rider_resources()
        rows=[{'support':'ring','pose':'pose_ring','item':'mg42_belt','parent':'body','position':[0,i*5,10],'rotation':[0,0,0],
               'yaw':[-9,9],'pitch':[0,9]} for i in range(10)]
        with tempfile.TemporaryDirectory() as temp:
            result=compile_assembly(resources,'hull','donor',{'rider_mgs':rows},temp,'mw_many')
        self.assertEqual([m['place'] for m in result['metadata']['rider_mgs']['mounts']],[f'mw_rider{i}' for i in range(1,11)])

    def test_passenger_mgs_need_a_door_and_a_known_mount(self):
        resources=self._rider_resources()
        row={'support':'ring','pose':'pose_ring','item':'mg42_belt','parent':'turret','position':[0,0,0],'rotation':[0,0,0],'yaw':[-9,9],'pitch':[0,9]}
        for bad,message in (({**row,'support':'nope'},'soporte'),({**row,'item':'k98'},'ametralladora'),({**row,'yaw':[9,-9]},'mínimo'),
                            ({**row,'parent':'missing'},'hueso')):
            with tempfile.TemporaryDirectory() as temp,self.assertRaisesRegex(CompileError,message):
                compile_assembly(resources,'hull','donor',{'rider_mgs':[bad]},temp,'mw_bad')
        resources.files['entity/hull/hull.def']=resources.files['entity/hull/hull.def'].replace(b'{boarder {door "emit1"} {link "emit1" "driver"}}',b'')
        with tempfile.TemporaryDirectory() as temp,self.assertRaisesRegex(CompileError,'puertas'):
            compile_assembly(resources,'hull','donor',{'rider_mgs':[row]},temp,'mw_bad')

    def test_weapon_place_role_lists_and_implicit_gunner(self):
        from workshop.compiler import _place_roles
        place=parse('{place "gun" {charger "charger charger2" minWorkers 1}}').children[0]
        self.assertEqual(_place_roles(place),{'charger','charger2'})
        self.assertEqual(_place_roles(place,implicit_gunner=True),{'gunner','charger','charger2'})

    def test_module_crew_is_declared_before_chassis_passenger_seats(self):
        # The engine seats crew in declaration order: with the Flak crew after
        # the BT-2 deck seats, soldiers sat as passengers and the gun stayed empty.
        resources=FakeResources()
        resources.files['properties/tank_crew.ext']=b'(define "seat_human" {placer {place "%place" {group "passenger"}}})'
        hull,donor='entity/hull/hull.def','entity/donor/donor.def'
        resources.files[hull]=resources.files[hull].replace(b'{mass 9000}',
            b'{placer {place "driver" {group "crew"}}}("seat_human" place(seat1))("seat_human" place(seat2)){mass 9000}')
        resources.files[donor]=resources.files[donor].replace(b'{gunner "gunner"}',b'{gunner "gunner"}{charger "commander"}')
        resources.files[donor]=resources.files[donor].replace(b'{mass 9000}',b'{placer {place "commander" {group "crew"}}}{mass 9000}')
        with tempfile.TemporaryDirectory() as temp:
            compile_assembly(resources,'hull','donor',{},temp,'vww_order')
            definition=(Path(temp)/'resource/entity/-vehicle/workshop/vww_order/vww_order.def').read_text()
            self.assertLess(definition.index('place "commander"'),definition.index('"seat_human"'))
            self.assertLess(definition.index('place "driver"'),definition.index('place "commander"'))

    def test_crew_macro_empty_and_block_arguments_are_substituted(self):
        # Sd.Kfz 222: ("crew_human" place(driver) bone() off({targetable})) left
        # "%bone"/"%off" in the definition and the game crashed reading it.
        resources=FakeResources()
        resources.files['properties/car_crew.ext']=(b'(define "crew_human" {placer {place "%place" {LinkBone "%bone"} '
                                                    b'{group "crew"} {turnoff {sensor} %off}}})')
        key='entity/hull/hull.def'
        resources.files[key]=resources.files[key].replace(b'{mass 9000}',
            b'("crew_human" place(driver) bone() off({targetable})){mass 9000}')
        with tempfile.TemporaryDirectory() as temp:
            compile_assembly(resources,'hull','donor',{},temp,'vww_macro')
            definition=(Path(temp)/'resource/entity/-vehicle/workshop/vww_macro/vww_macro.def').read_text()
            self.assertIn('{place "driver" {LinkBone ""}',definition)
            self.assertIn('{turnoff {sensor} {targetable}}',definition)
            self.assertNotIn('%',definition)

    def test_internal_turret_crew_without_bones_transfers_as_places(self):
        # Pz III / BT-2 / 35(t): turret crew are internal places with no LinkBone.
        resources=FakeResources()
        key='entity/donor/donor.def'
        resources.files[key]=resources.files[key].replace(b'{gunner "gunner"}',b'{gunner "gunner"}{charger "commander"}')
        resources.files[key]=resources.files[key].replace(b'{mass 9000}',b'{placer {place "commander" {group "crew"}}}{mass 9000}')
        with tempfile.TemporaryDirectory() as temp:
            result=compile_assembly(resources,'hull','donor',{},temp,'vww_inner')
            definition=(Path(temp)/'resource/entity/-vehicle/workshop/vww_inner/vww_inner.def').read_text()
            self.assertIn('place "commander"',definition)
            self.assertIn('commander',result['metadata']['crew_roles'])

    def test_chassis_keeps_its_boarding_animations_over_donor_doors(self):
        # In game the driver ended up on a passenger seat: the armored-car
        # donor's "left" entry (with its own door clip) replaced the tank's.
        resources=FakeResources()
        hull,donor='entity/hull/hull.def','entity/donor/donor.def'
        resources.files[hull]=resources.files[hull].replace(b'{mass 9000}',
            b'{boarder {anm "left" {forward {begin "board_tank"}}}}{placer {place "driver" {group "crew"}}}'
            b'{boarder {door "emit1"}{link "emit1" "driver" {anm "left"}}}{mass 9000}')
        resources.files[donor]=resources.files[donor].replace(b'{mass 9000}',
            b'{boarder {anm "left" {forward {begin "board_car"} {base "doorleft"}}}'
            b'{anm "hatch" {forward {begin "climb"} {base "doorright"}}}}'
            b'{placer {place "gunner" {LinkBone "gunner"} {group "crew"}}}'
            b'{boarder {door "emit1"}{link "emit1" "gunner" {anm "left"}}}{mass 9000}')
        with tempfile.TemporaryDirectory() as temp:
            compile_assembly(resources,'hull','donor',{},temp,'vww_board')
            definition=(Path(temp)/'resource/entity/-vehicle/workshop/vww_board/vww_board.def').read_text()
            self.assertIn('board_tank',definition)
            self.assertNotIn('board_car',definition)
            self.assertIn('"climb"',definition)
            self.assertNotIn('doorleft',definition)
            self.assertNotIn('doorright',definition)
            self.assertIn('{link "emit1" "gunner"',definition)


if __name__ == '__main__':
    unittest.main()


class CrashGuardTests(unittest.TestCase):
    def test_bone_block_for_a_missing_bone_is_rejected_before_install(self):
        # "Bone mgun_a_rot1 not found" closed the game on 13/10.
        from workshop.compiler import _validate_bone_blocks
        model = b'{skeleton {bone "basis" {bone "body"}}}'
        good = {"v.mdl": model, "v.def": b'{game_entity {extension "v.mdl"} {bone "Body" {speed2 1}}}'}
        _validate_bone_blocks(good)  # case does not matter, as in the engine
        bad = {"v.mdl": model, "v.def": b'{game_entity {extension "v.mdl"} {bone "mgun_a_rot1" {limits -5 5}}}'}
        with self.assertRaisesRegex(CompileError, "Bone not found"):
            _validate_bone_blocks(bad)

    def test_visors_never_look_from_a_bone_that_was_removed(self):
        from workshop.integrity import dangling
        resources = FakeResources()
        key = 'entity/hull/hull.def'
        resources.files[key] = resources.files[key].replace(
            b'{mass 9000}', b'{sensor {visor "main" {bone "gunner"}} {visor "driver" {bone "driver"}}}{mass 9000}')
        with tempfile.TemporaryDirectory() as temp:
            compile_assembly(resources, 'hull', 'donor', {}, temp, 'vww_visor')
            folder = Path(temp)/'resource/entity/-vehicle/workshop/vww_visor'
            root = parse((folder/'vww_visor.def').read_text()).find('game_entity')
            model = parse_model((folder/'vww_visor.mdl').read_text())
            self.assertEqual([ref for ref in dangling(root, model) if ref[0] == 'visor-bone'], [])
