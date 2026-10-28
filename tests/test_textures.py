import struct
import unittest

from workshop.textures import decode_dds, diffuse_path, png


class FakeResources:
    def __init__(self, files):
        self.files = files

    def read(self, path):
        return self.files[path]

    def exists(self, path):
        return path in self.files


def dds(fourcc, width, height, blocks, mips=1):
    header = bytearray(128)
    header[:4] = b"DDS "
    struct.pack_into("<IIIII", header, 4, 124, 0x1007, height, width, 0)
    struct.pack_into("<I", header, 28, mips)
    struct.pack_into("<I", header, 76, 32)
    header[84:88] = fourcc
    return bytes(header) + blocks


class TextureTests(unittest.TestCase):
    def test_diffuse_paths_use_the_texture_root_or_the_material_folder(self):
        resources = FakeResources({"a/b/base.mtl": b'{material bump {diffuse "$/_vehicle/tank"}}',
                                   "a/b/decal.mtl": b'{material simple {diffuse "1"}}',
                                   "texture/common/_vehicle/tank.dds": b"", "a/b/1.dds": b""})
        self.assertEqual(diffuse_path(resources, "a/b/base.mtl"), "texture/common/_vehicle/tank.dds")
        self.assertEqual(diffuse_path(resources, "a/b/decal.mtl"), "a/b/1.dds")

    def test_dxt1_block_and_smallest_fitting_mip(self):
        red, blue = 0xF800, 0x001F
        block = struct.pack("<HHI", red, blue, 0b01 * 0x55555555)  # every texel = colour 1 (blue)
        mip0, mip1 = block * 4, block  # 8x8 then 4x4
        width, height, rgba = decode_dds(dds(b"DXT1", 8, 8, mip0 + mip1, mips=2), limit=4)
        self.assertEqual((width, height), (4, 4))
        self.assertEqual(rgba[:4], bytes((0, 0, 255, 255)))
        self.assertTrue(png(width, height, rgba).startswith(b"\x89PNG"))

    def test_dxt5_alpha(self):
        alpha = bytes((255, 0)) + (0).to_bytes(6, "little")  # index 0 -> 255
        colour = struct.pack("<HHI", 0xFFFF, 0, 0)
        _, _, rgba = decode_dds(dds(b"DXT5", 4, 4, alpha + colour))
        self.assertEqual(rgba[:4], bytes((255, 255, 255, 255)))


if __name__ == "__main__":
    unittest.main()
