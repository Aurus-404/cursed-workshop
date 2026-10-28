import struct
import unittest

from workshop.crew_model import _clip_locals, _pose_registry, _read_skinned, pose_for, skin_deformations, skin_material, posed_soldier, SOLDIER, SKELETON


def skinned_ply():
    """Two triangles' worth of a skinned EPLY: SKIN names, one MESH with a
    bone table, 56-byte vertices (slots 0-based into the table, table value
    v -> SKIN name v-1), then INDX."""
    names = [b"body", b"head"]
    skin = b"SKIN" + struct.pack("<I", len(names)) + b"".join(bytes([len(n)]) + n for n in names)
    mesh = b"MESH" + struct.pack("<4I", 0x1118, 0, 1, 0xd15) + bytes([8]) + b"skin.mtl" + bytes([2, 1, 2])
    rows = b""
    for position, weight, slots in (((0, 0, 0), 1.0, (0, 0)), ((1, 0, 0), 0.75, (1, 0)), ((0, 1, 0), 1.0, (1, 0))):
        rows += struct.pack("<4f", *position, weight) + bytes([*slots, 0, 0]) + struct.pack("<3f2f4f", 0, 0, 1, 0.5, 0.5, 1, 0, 0, 1)
    return b"EPLY" + skin + mesh + b"VERT" + struct.pack("<IHH", 3, 56, 7) + rows + b"INDX" + struct.pack("<I3H", 3, 0, 1, 2)


class FakeResources:
    def __init__(self, files):
        self.files = files

    def read(self, path):
        return self.files[path]

    def exists(self, path):
        return path in self.files


class CrewModelTests(unittest.TestCase):
    def test_pose_exposes_the_animated_body_anchor_in_actor_space(self):
        from unittest.mock import patch
        from workshop.geometry import euler_matrix
        mdl=b'{skeleton {bone "basis" {position 10 20 30}{orientation 1 0 0 0 -1 0 0 0 1}{bone "body" {position 1 2 3}}{bone "skin"}}}'
        resources=FakeResources({SOLDIER:mdl,SKELETON:mdl,'clip':b'clip'})
        with patch('workshop.crew_model._skin_meshes',return_value=[('skin.ply',skinned_ply())]), \
             patch('workshop.crew_model.pose_for',return_value=('clip','begin')), \
             patch('workshop.crew_model._clip_locals',return_value={'body':euler_matrix([0,0,0],[4,40,15])}):
            result=posed_soldier(resources,'offset_pose')
        self.assertEqual(result['pose_origin'],[4,40,15])
        self.assertTrue(result['meshes'])

    def test_native_clockwise_faces_match_preview_normals_without_mirroring_vertices_or_uv(self):
        from unittest.mock import patch
        mdl=b'{skeleton {bone "basis" {orientation 1 0 0 0 -1 0 0 0 1}{bone "skin"}}}'
        resources=FakeResources({SOLDIER:mdl,SKELETON:mdl})
        raw=skinned_ply()[:-6]+struct.pack('<3H',0,2,1)
        with patch('workshop.crew_model._skin_meshes',return_value=[('skin.ply',raw)]):
            mesh=posed_soldier(resources)['meshes'][0]
        self.assertEqual(_read_skinned(raw)[0]['indices'],[0,1,2],'decoder remaps vertex IDs in native order')
        self.assertEqual(mesh['indices'],[0,2,1])
        self.assertEqual(mesh['positions'],[0,0,0,0,1,0,1,0,0],'only winding changes, not vertex handedness')
        self.assertEqual(mesh['normals'],[0,0,1]*3)
        self.assertEqual(mesh['uv'],[.5,.5]*3)

    def test_attached_soldier_replaces_root_basis_after_skinning(self):
        from workshop.geometry import parse_model, point, multiply, euler_matrix
        from unittest.mock import patch
        # Translated and reflected actor root; a rotated/translated seat. The
        # end-to-end skin output must attach once, in posed and fallback paths.
        mdl=b'{skeleton {bone "basis" {position 2 3 4}{orientation 1 0 0 0 -1 0 0 0 1}{bone "skin"}}}'
        resources=FakeResources({SOLDIER:mdl,SKELETON:mdl})
        root=parse_model(mdl)['bones']['basis']['world']
        seat=euler_matrix([0,0,73],[10,20,30])
        for posed in (False,True):
            with self.subTest(posed=posed), patch('workshop.crew_model._skin_meshes',return_value=[('skin.ply',skinned_ply())]), patch('workshop.crew_model.pose_for',return_value=('clip','begin') if posed else None), patch('workshop.crew_model._clip_locals',return_value={'basis':root}):
                if posed:resources.files['clip']=b'clip'
                meshes=posed_soldier(resources,'seat')['meshes']
                self.assertEqual(meshes[0]['positions'],[0,0,0,1,0,0,0,1,0])
                for offset in range(0,9,3):
                    actual=point(multiply(seat,root),meshes[0]['positions'][offset:offset+3])
                    expected=point(multiply(seat,root),[(0,0,0),(1,0,0),(0,1,0)][offset//3])
                    for a,b in zip(actual,expected):self.assertAlmostEqual(a,b)

    def test_skin_uses_uniform_bind_not_reference_rig_rest(self):
        from workshop.geometry import parse_model, point, euler_matrix
        uniform=parse_model('{skeleton {bone "hand" {position 4 3 2} {orientation 0 1 0 -1 0 0 0 0 1}}}')
        target=euler_matrix([20,30,40],[8,9,10])
        deform=skin_deformations(uniform,{'hand':target})['hand']
        # The skin point sitting on this model's wrist must reach the animated wrist.
        for a,b in zip(point(deform,[4,3,2]),[8,9,10]):self.assertAlmostEqual(a,b)

    def test_shared_skin_uses_selected_uniform_material_override(self):
        from types import SimpleNamespace
        resources=SimpleNamespace(exists=lambda p:p in {'entity/uniform/skin.mtl','entity/shared/skin.mtl'})
        self.assertEqual(skin_material(resources,'entity/uniform/model.mdl','entity/shared/body.ply','skin.mtl'),'entity/uniform/skin.mtl')
        self.assertIsNone(skin_material(resources,'entity/uniform/model.mdl','entity/shared/body.ply','absent.mtl'))

    def test_skinned_mesh_bone_slots_and_weights(self):
        parts = _read_skinned(skinned_ply())
        self.assertEqual(len(parts), 1)
        part = parts[0]
        self.assertEqual(part["material"], "skin.mtl")
        self.assertEqual(part["indices"], [0, 1, 2])
        self.assertEqual(part["vertices"][0][3], [("body", 1.0)])
        self.assertEqual(part["vertices"][1][3], [("head", 0.75), ("body", 0.25)])

    def test_pose_names_resolve_through_the_animation_registry(self):
        registry = (b'{animation {blend "pose_seat_cannon_left" {pose "seat_crew_cannon" begin}}'
                    b'{blend "pose_seat_cannon_right" {pose "seat_crew_cannon" end}} {sequence "seat_commander_hatch"}}')
        resources = FakeResources({"properties/animation/human/_reg_vehicle_crew.inc": registry,
                                   "properties/animation/human/seat_crew_cannon.anm": b"",
                                   "properties/animation/human/seat_commander_hatch.anm": b""})
        self.assertEqual(_pose_registry(resources)["pose_seat_cannon_right"], ("seat_crew_cannon", "end"))
        self.assertEqual(pose_for(resources, "pose_seat_cannon_left"), ("properties/animation/human/seat_crew_cannon.anm", "begin"))
        self.assertEqual(pose_for(resources, "seat_commander_hatch")[1], "begin")
        self.assertIsNone(pose_for(resources, "unknown_pose"))
        self.assertIsNone(pose_for(resources, None))

    def test_clip_keys_hold_their_last_value(self):
        names = [b"body", b"head"]
        raw = b"EANM" + struct.pack("<I", 0) + b"FRMS" + struct.pack("<I", 3) + b"BMAP" + struct.pack("<I", 2)
        raw += b"".join(struct.pack("<I", len(n)) + n for n in names)
        raw += b"FRM2" + struct.pack("<HB", 0, 2) + bytes([0]) + struct.pack("<H", 1) + struct.pack("<3f", 1, 2, 3)
        raw += bytes([1]) + struct.pack("<H", 2) + struct.pack("<3f", 0, 0, 0)
        raw += b"FRM2" + struct.pack("<HB", 2, 1) + bytes([0]) + struct.pack("<H", 1) + struct.pack("<3f", 4, 5, 6)
        begin, end = _clip_locals(raw, "begin"), _clip_locals(raw, "end")
        self.assertEqual(begin["body"][0], (1, 2, 3))
        self.assertEqual(end["body"][0], (4, 5, 6))
        self.assertEqual(end["head"][1], (0, 0, 0))  # held from frame 0


if __name__ == "__main__":
    unittest.main()
