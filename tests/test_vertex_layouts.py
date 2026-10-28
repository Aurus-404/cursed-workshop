import struct
import unittest

from workshop.geometry import read_mesh, GeometryError


def mesh(fvf, vertex):
    data = struct.pack('<' + 'f'*len(vertex), *vertex)
    return (b'EPLYMESH' + struct.pack('<5I', fvf, 0, 3, 0, 0) + b'\x05a.mtl'
            + b'VERT' + struct.pack('<IHH', 3, len(data), 7) + data*3
            + b'INDX' + struct.pack('<I3H', 3, 0, 1, 2))


class VertexLayoutTests(unittest.TestCase):
    def test_skinned_and_rigid_same_stride_have_different_normal_and_uv_offsets(self):
        rigid = mesh(0x212, [1, 2, 3, 0, 0, 1, .25, .5, .8, .9, 1, 0, 0, 1])
        skinned = mesh(0x1118, [1, 2, 3, 1, 0, 0, 0, 1, .25, 10.5, 1, 0, 0, 1])
        for raw, uv in ((rigid, [.25, .5]), (skinned, [.25, 10.5])):
            with self.subTest(uv=uv):
                result = read_mesh(raw)
                self.assertEqual(result['positions'], [1, 2, 3]*3)
                self.assertEqual(result['normals'], [0, 0, 1]*3)
                self.assertEqual(result['uv'], uv*3)

    def test_rigid_standard_and_tangent_layouts_keep_first_uv_channel(self):
        for tail in ([], [1, 0, 0, 1]):
            result = read_mesh(mesh(0x112, [1, 2, 3, 0, 0, 1, .25, .5] + tail))
            self.assertEqual(result['uv'], [.25, .5]*3)

    def test_invalid_declared_layout_cannot_read_past_the_vertex(self):
        with self.assertRaises(GeometryError):
            read_mesh(mesh(0x1118, [1, 2, 3, 0, 0, 1, .25, .5]))


if __name__ == '__main__':
    unittest.main()
