import struct
import unittest

from workshop.armor_view import read_volume_mesh, _box, _thickness, armor_volumes, FACES
from workshop.geometry import parse_model
from workshop.syntax import parse, entity_root


def vol(verts, indices, sides):
    raw = b'EVLM' + b'VERT' + struct.pack('<I', len(verts)) + b''.join(struct.pack('<3f', *v) for v in verts)
    raw += b'INDX' + struct.pack('<I', len(indices)) + struct.pack('<%dH' % len(indices), *indices)
    return raw + b'SIDE' + struct.pack('<I', len(sides)) + bytes(sides)


class Resources:
    def __init__(self, files):
        self.files = files
    def read(self, path):
        return self.files[path]
    def exists(self, path):
        return path in self.files


class ArmorViewTests(unittest.TestCase):
    def test_volume_mesh_keeps_the_face_of_every_triangle(self):
        verts, indices, faces = read_volume_mesh(vol([(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)], [0, 1, 2, 0, 2, 3], [2, 5]))
        self.assertEqual(len(verts), 4)
        self.assertEqual(indices, [0, 1, 2, 0, 2, 3])
        self.assertEqual([FACES[c] for c in faces], ['top', 'rear'])
        with self.assertRaises(ValueError):
            read_volume_mesh(b'NOPE')

    def test_box_faces_follow_the_native_hull_convention(self):
        verts, indices, faces = _box([4, 2, 2])
        normal = {}
        for t, code in enumerate(faces):
            a, b, c = (verts[indices[3*t+k]] for k in range(3))
            centre = [(a[i]+b[i]+c[i])/3 for i in range(3)]
            axis = max(range(3), key=lambda i: abs(centre[i]) / [2, 1, 1][i])
            normal[FACES[code]] = ('+' if centre[axis] > 0 else '-') + 'xyz'[axis]
        self.assertEqual(normal, {'front': '+x', 'rear': '-x', 'left': '+y', 'right': '-y', 'top': '+z', 'bottom': '-z'})

    def test_thickness_reads_the_default_and_each_face(self):
        node = parse('{volume "body" {thickness 50 {front 80} {top 40}}}').find('volume')
        self.assertEqual(_thickness(node), {'default': 50.0, 'front': 80.0, 'top': 40.0})
        self.assertIsNone(_thickness(parse('{volume "x"}').find('volume')))

    def test_only_the_shown_part_and_declared_volumes_are_returned(self):
        model = parse_model(b'{skeleton {bone "basis" {bone "body" {bone revolute "turret"}}}}'
                            b'{volume "body" {bone "body"} {polyhedron "body.vol"}}'
                            b'{volume "turret" {bone "turret"} {box 2 2 2}}'
                            b'{volume "ammo" {bone "body"} {box 1 1 1}}')
        root = entity_root(parse('{game_entity {volume "body" {thickness 50 {front 80}} {material "steel"}}'
                                 '{volume "turret" {thickness 30} {CastSteel 0.8}}}'))
        resources = Resources({'entity/t/body.vol': vol([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2], [1])})
        hull = armor_volumes(resources, root, model, 'entity/t/t.mdl', lambda bone: bone != 'turret')
        self.assertEqual([v['name'] for v in hull], ['body'], 'undeclared volumes (ammo) carry no armour')
        self.assertEqual(hull[0]['thickness'], {'default': 50.0, 'front': 80.0})
        self.assertEqual(hull[0]['faces'], [1])
        turret = armor_volumes(resources, root, model, 'entity/t/t.mdl', lambda bone: bone == 'turret')
        self.assertEqual([v['name'] for v in turret], ['turret'])
        self.assertEqual(turret[0]['cast'], 0.8)
        self.assertEqual(len(turret[0]['indices']) // 3, 12)


if __name__ == '__main__':
    unittest.main()
