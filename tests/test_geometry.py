import math
import struct
import unittest
from workshop.geometry import read_mesh, mesh_materials, parse_model, point, multiply, inverse, euler_matrix, GeometryError, mount_seat, seat_offset


def eply():
    vertices = [(-1,0,0,0,0,1,0,0),(1,0,0,0,0,1,1,0),(0,1,0,0,0,1,0,1)]
    return b'EPLYBNDS'+struct.pack('<6f',-1,0,0,1,1,0)+b'VERT'+struct.pack('<IHH',3,32,3)+b''.join(struct.pack('<8f',*v) for v in vertices)+b'INDX'+struct.pack('<I3H',3,0,1,2)


class GeometryTests(unittest.TestCase):
    def test_numbered_hull_under_revolute_root_keeps_its_real_turret(self):
        from workshop.geometry import build_preview, replaceable_mounts, recipient_mount_name
        # B1-style exporters wrap the numbered hull and its weapons in a
        # revolute basis. The wrapper must not hide the actual turret target.
        text = ('{skeleton {bone revolute "basis" {bone "body1" {VolumeView "hull.ply"}'
                '{bone revolute "turret" {VolumeView "turret.ply"}'
                '{bone revolute "gun_rot4" {bone "mgun"}}}'
                '{bone revolute "hull_yaw" {bone "gun2"}}}}}')
        model = parse_model(text)
        self.assertIn('basis', model['mounts'])
        self.assertNotIn('basis', replaceable_mounts(model))
        self.assertIn('turret', replaceable_mounts(model))
        self.assertEqual(recipient_mount_name(model), 'turret')
        with self.assertRaises(GeometryError):
            recipient_mount_name(model, 'basis')
        class Resources:
            def resolve_template(self, name):return {'model_path':'entity/test.mdl','definition_path':'entity/test.def'}
            def read(self, path):
                if path.endswith('.mdl'):return text.encode()
                if path.endswith('.def'):return b'{game_entity}'
                return eply()
        data = build_preview(Resources(), 'test', 'hull')
        self.assertIn('turret', data['mounts'])
        self.assertNotIn('basis', data['mounts'])
        self.assertEqual([m['bone'] for m in data['meshes']], ['body1'])

    def test_structural_rotation_frame_is_never_a_replaceable_turret(self):
        from workshop.geometry import replaceable_mounts, recipient_mount_name, build_preview
        model_text = ('{skeleton {bone revolute "frame" {bone "body" {VolumeView "hull.ply"}'
                      '{bone revolute "mgun_h_rot1" {bone revolute "mgun_h_rot2"'
                      '{bone "mgun" {VolumeView "gun.ply"}}}}}}}')
        model = parse_model(model_text)
        self.assertIn('frame', model['mounts'])
        self.assertNotIn('frame', replaceable_mounts(model))
        self.assertEqual(recipient_mount_name(model), '@free')
        with self.assertRaisesRegex(GeometryError, 'chasis completo'):
            recipient_mount_name(model, 'frame')
        class Resources:
            def resolve_template(self, name):return {'model_path':'entity/test.mdl','definition_path':'entity/test.def'}
            def read(self, path):
                if path.endswith('.mdl'):return model_text.encode()
                if path.endswith('.def'):return b'{game_entity}'
                return eply()
        preview = build_preview(Resources(), 'test', 'hull')
        self.assertEqual(preview['anchor_name'], '@free')
        self.assertEqual(len(preview['meshes']), 2)
        self.assertNotIn('frame', preview['mounts'])
        self.assertIn('@free', preview['mounts'])

    def test_default_recipient_mount_keeps_a_real_turret_and_rejects_wrapped_hull(self):
        from workshop.geometry import recipient_mount_name, replaceable_mounts
        model = parse_model('{skeleton {bone "root" {bone revolute "frame" {bone "body"'
                            '{bone revolute "turret" {bone "gun"}}}}}}')
        self.assertEqual(recipient_mount_name(model), 'turret')
        self.assertEqual(replaceable_mounts(model), ['turret'])
        for invalid in ('root', 'frame', 'body'):
            with self.subTest(invalid=invalid), self.assertRaises(GeometryError):
                recipient_mount_name(model, invalid)

    def test_a_module_whose_turret_carries_nothing_visible_moves_as_a_whole(self):
        # Lazarus, MACE T91: the rocket tubes hang from the body; the turret
        # and gun bones only aim. As a donor its turret alone showed nothing.
        from workshop.geometry import donor_mount_name
        launcher = parse_model('{skeleton {bone revolute "basis" {bone "body" {VolumeView "body.ply"} {bone "rocket" {VolumeView "rocket.ply"}}'
                               '{bone revolute "turret" {bone revolute "gun_rot" {bone "gun"}}}}}}')
        self.assertEqual(launcher['anchor_name'], 'turret')
        self.assertEqual(donor_mount_name(launcher), 'basis')
        tank = parse_model('{skeleton {bone "basis" {bone "body" {VolumeView "body.ply"} {bone revolute "turret" {VolumeView "turret.ply"} {bone revolute "gun_rot" {bone "gun"}}}}}}')
        self.assertEqual(donor_mount_name(tank), 'turret')

    def test_numbered_auxiliary_turrets_are_mounts(self):
        # TheOppsAreOntoMe: the T-35's 45 mm turrets carry "gun_rot1"/"gun1"
        # and could not be swapped; the MG turrets (mgun_*) could.
        model = parse_model('{skeleton {bone "basis" {bone "body"'
                            '{bone revolute "turret" {bone revolute "gun_rot" {bone "gun"}}}'
                            '{bone revolute "aux_turret1" {bone revolute "gun_rot1" {bone "gun1"}}}'
                            '{bone revolute "aux_turret2" {bone revolute "gun2_rot" {bone "gun2"}}}'
                            '{bone revolute "aux_turret3" {bone revolute "mgun_3_rot1" {bone "mgun3"}}}'
                            '{bone revolute "hatch" {bone "gunner"}}}}}')
        mounts = parse_model.__globals__['replaceable_mounts'](model)
        for name in ('turret', 'aux_turret1', 'aux_turret2', 'aux_turret3'):
            self.assertIn(name, mounts)
        self.assertNotIn('hatch', mounts, 'a crew seat bone is not a weapon')

    def test_winding_reversal_changes_indices_only_in_every_chunk(self):
        from workshop.geometry import reverse_mesh_winding
        raw=eply()+eply()[4:]+b'UNCHANGED_TRAILER'
        before=read_mesh(raw);changed=reverse_mesh_winding(raw);after=read_mesh(changed)
        self.assertEqual(after['indices'],[0,2,1,3,5,4])
        for field in ('positions','normals','uv','submeshes','bounds'):
            self.assertEqual(before[field],after[field])
        self.assertTrue(changed.endswith(b'UNCHANGED_TRAILER'))
        self.assertEqual(reverse_mesh_winding(changed),raw)
        with self.assertRaises(GeometryError):reverse_mesh_winding(eply()[:-2])

    def test_vertices_no_triangle_draws_do_not_count_for_bounds(self):
        # MACE nbfz/pz2j body.ply ship a few unused vertices at 1e22: the game never
        # draws them, and the 3D view framed the vehicle out of sight.
        vertices=[(-1,0,0,0,0,1,0,0),(1,0,0,0,0,1,1,0),(0,1,0,0,0,1,0,1),(1e22,0,1e22,0,0,1,0,0)]
        raw=(b'EPLYBNDS'+struct.pack('<6f',-1,0,0,1,1,0)+b'VERT'+struct.pack('<IHH',4,32,3)
             +b''.join(struct.pack('<8f',*v) for v in vertices)+b'INDX'+struct.pack('<I3H',3,0,1,2))
        mesh=read_mesh(raw)
        self.assertEqual(mesh['vertex_count'],4)
        self.assertEqual(mesh['unused_vertices'],1)
        self.assertLess(max(abs(v) for v in mesh['positions']),2)

    def test_attached_actor_replaces_standalone_reflected_root(self):
        from workshop.geometry import attached_actor_frame
        model=parse_model('{skeleton {bone "weapon" {orientation 1 0 0 0 -1 0 0 0 1} {position 2 3 4}}}')
        socket=euler_matrix([90,0,25],[10,20,30])
        root=model['bones']['weapon']['world']
        native=[1,2,3]
        actual=point(attached_actor_frame(model,socket),point(root,native))
        for a,b in zip(actual,point(socket,native)):self.assertAlmostEqual(a,b)

    def test_native_layout_and_index_validation(self):
        raw = eply()
        result = read_mesh(raw)
        self.assertEqual(result['vertex_count'],3)
        self.assertEqual(result['indices'],[0,1,2])
        self.assertEqual(result['uv'],[0,0,1,0,0,1])
        with self.assertRaisesRegex(GeometryError,'missing vertex'):
            read_mesh(raw[:-2]+struct.pack('<H',30))
        with self.assertRaises(GeometryError):
            read_mesh(raw[:-8])

    def test_mesh_materials_four_and_five_field_headers(self):
        # Tactical number plates (panzer3e enumerator1.ply) carry four uint32
        # fields before the material path; most meshes carry five.
        five = b'MESH'+struct.pack('<5I',0x112,0,1,0,3)+bytes([8])+b'hull.mtl'
        four = b'MESH'+struct.pack('<4I',0x112,0,2,0x404)+bytes([11])+b'number1.mtl'
        raw = b'EPLY'+five+four+b'VERT'
        found = mesh_materials(raw)
        self.assertEqual([name for _, name in found], ['hull.mtl', 'number1.mtl'])
        self.assertEqual([offset for offset, _ in found], [raw.index(b'hull.mtl')-1, raw.index(b'number1.mtl')-1])

    def test_shipped_nan_vertices_tolerated(self):
        # T-20 dt29.ply has NaN U coordinates and a Churchill mesh NaN positions;
        # the game draws both. Fitting uses a valid vertex of the same chunk.
        nan = float('nan')
        raw = eply().replace(struct.pack('<8f',1,0,0,0,0,1,1,0), struct.pack('<8f',1,0,0,0,0,1,nan,0))
        self.assertEqual(read_mesh(raw)['uv'],[0,0,0,0,0,1])
        raw = eply().replace(struct.pack('<8f',1,0,0,0,0,1,1,0), struct.pack('<8f',nan,0,0,0,0,1,1,0))
        mesh = read_mesh(raw)
        self.assertEqual(mesh['positions'][3:6],[-1,0,0])
        self.assertEqual(mesh['invalid_vertices'],1)
        self.assertTrue(all(map(math.isfinite, mesh['positions'])))

    def test_rotated_parent_matrix_and_typed_bone(self):
        model = parse_model('{skeleton {Bone "basis" {Orientation 0 1 0 -1 0 0 0 0 1} {Bone "body" {Position 10 0 0} {Bone revolute "turret" {Position 1 2 3} {Bone "gun"}}}}}')
        self.assertEqual(model['anchor'],[-2,11,3])
        world = model['bones']['turret']['world']
        self.assertEqual(point(inverse(world), point(world,[2,3,4])),[2,3,4])

    def test_lod_views_count_as_the_bone_meshes(self):
        # Sd.Kfz 222 wheels: {LODView {VolumeView "wheelr1.ply"} {VolumeView "wheelr1_lod1.ply"}}
        model = parse_model('{skeleton {bone "wheel" {LODView {VolumeView "w.ply"} {VolumeView "w_lod1.ply"}} {VolumeView "hub.ply"}}}')
        self.assertEqual(model['bones']['wheel']['assets'], ['hub.ply', 'w.ply'])

    def test_repeated_bone_names_resolve_to_the_first(self):
        # ARL-44 ships two "visor1" locators (turret and hull); the game accepts it.
        model = parse_model('{skeleton {bone "body" {bone "visor1" {Position 1 0 0}} {bone "turret" {bone "visor1" {Position 2 0 0}}}}}')
        self.assertEqual(model['bones']['visor1']['parent'], 'body')
        self.assertEqual([(d['name'], d['parent']) for d in model['duplicates']], [('visor1', 'turret')])
        self.assertIn('visor1', model['document'].render().split('turret', 1)[1])  # left in the file

    def test_euler_order_matches_xyz_applied_column_vectors(self):
        self.assertAlmostEqual(point(euler_matrix([90,90,0]),[0,1,0])[0],1)

    def test_seat_offset_rests_module_on_recipient_roof(self):
        def plate(z, half):
            vertices = [(-half,-half,z,0,0,1,0,0),(half,-half,z,0,0,1,1,0),(0,half,z,0,0,1,0,1)]
            return b'EPLY'+b'VERT'+struct.pack('<IHH',3,32,3)+b''.join(struct.pack('<8f',*v) for v in vertices)+b'INDX'+struct.pack('<I3H',3,0,1,2)
        files = {'a/roof.ply': plate(2, 5), 'a/shell.ply': plate(4, 5), 'a/mast.ply': plate(50, 1),
                 'b/roof.ply': plate(-3, 5), 'b/shell.ply': plate(-1, 5)}
        class Resources:
            def read(self, path): return files[path]
        # Recipient turret bone sits at the roof; donor's sits 10 units below it.
        recipient = parse_model('{skeleton {bone "body" {VolumeView "roof.ply"} {bone "antenna" {VolumeView "mast.ply"}} {bone revolute "turret" {Position 0 0 2} {VolumeView "shell.ply"} {bone "gun"}}}}')
        donor = parse_model('{skeleton {bone "body" {VolumeView "roof.ply"} {bone revolute "turret" {Position 0 0 -13} {VolumeView "shell.ply"} {bone "mgun"}}}}')
        self.assertEqual(mount_seat(Resources(), recipient, 'a/x.mdl', 'turret'), 0)
        self.assertEqual(mount_seat(Resources(), donor, 'b/x.mdl', 'turret'), 10)
        self.assertEqual(seat_offset(Resources(), recipient, 'a/x.mdl', 'turret', donor, 'b/x.mdl', 'turret'), -10)


    def test_free_mount_spots_turret_roof_and_hatches(self):
        from workshop.geometry import free_mount_spec, free_mount_options, free_spot_parent
        model = parse_model('{skeleton {bone "basis" {bone "body" {VolumeView "b.ply"} {bone "hatch_engine" {VolumeView "h.ply"}}'
                            ' {bone "turret" {VolumeView "t.ply"} {bone "hatch_commander" {VolumeView "c.ply"}}}}}}')
        self.assertEqual(free_mount_spec('@free'), (True, None))
        self.assertEqual(free_mount_spec('@free:hatch_commander'), (True, 'hatch_commander'))
        self.assertEqual(free_mount_spec('turret'), (False, None))
        self.assertEqual(free_mount_options(model), ['@free', '@free:turret', '@free:hatch_engine', '@free:hatch_commander'])
        self.assertEqual(free_spot_parent(model, 'turret'), 'turret')            # turns with the turret
        self.assertEqual(free_spot_parent(model, 'hatch_commander'), 'turret')   # the lid itself opens
        self.assertEqual(free_spot_parent(model, 'hatch_engine'), 'body')


if __name__ == '__main__':
    unittest.main()
