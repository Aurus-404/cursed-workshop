import base64
import struct
import unittest

from workshop.icons import ICON_KINDS, ICON_STRIPS, IconError, compose, decode_render, read_tga, write_tga, chassis_strips


def solid(width, height, rgba):
    return bytes(rgba) * (width * height)


class FakeResources:
    def __init__(self, files):
        self.files = files

    def exists(self, path):
        return path in self.files

    def read_bytes(self, path):
        return self.files[path]


class IconTests(unittest.TestCase):
    def test_tga_round_trip_and_rle(self):
        pixels = bytes(range(16)) * 2  # 2x4 RGBA
        width, height, rgba = read_tga(write_tga(4, 2, pixels))
        self.assertEqual((width, height, bytes(rgba)), (4, 2, pixels))
        # RLE, bottom-left origin, 24 bit: one run of 2 red pixels per row.
        header = struct.pack("<BBBHHBHHHHBB", 0, 0, 10, 0, 0, 0, 0, 0, 2, 2, 24, 0)
        rows = [bytes([0x81, 0, 0, 255]), bytes([0x81, 0, 255, 0])]  # bottom row red, top row green
        width, height, rgba = read_tga(header + b"".join(rows))
        self.assertEqual(bytes(rgba[:4]), bytes((0, 255, 0, 255)))  # top-left is green
        self.assertEqual(bytes(rgba[-4:]), bytes((255, 0, 0, 255)))
        with self.assertRaises(IconError):
            read_tga(b"\0" * 10)

    def test_render_payload_is_exactly_sized(self):
        width, height = ICON_KINDS["unit_icon"]
        good = "rgba:" + base64.b64encode(b"\0" * (width * height * 4)).decode()
        self.assertEqual(len(decode_render(good, width, height)), width * height * 4)
        for bad in ("rgba:" + base64.b64encode(b"\0" * 10).decode(), "data:image/png;base64,AAAA", None):
            with self.assertRaises(IconError):
                decode_render(bad, width, height)

    def test_compose_draws_only_the_new_vehicle_in_four_states(self):
        width, height = ICON_KINDS["unit_icon_small"]
        render = bytearray(width * height * 4)
        cx, cy = width // 2, height // 2
        render[(cy * width + cx) * 4:(cy * width + cx) * 4 + 4] = bytes((100, 100, 100, 255))
        icons = compose("unit_icon_small", bytes(render))
        self.assertEqual(len(icons), 4)
        decoded = [read_tga(raw)[2] for raw in icons]
        at = lambda img, x, y: tuple(img[(y * width + x) * 4:(y * width + x) * 4 + 4])
        self.assertEqual(at(decoded[0], cx, cy), (100, 100, 100, 255))   # vehicle drawn
        corner = at(decoded[0], 0, 0)
        self.assertLess(max(corner[:3]), 60)                             # plain dark plate, no emblem
        self.assertEqual(at(decoded[0], 2, 2)[3], 0)  # native small texture margin stays transparent
        self.assertEqual(at(decoded[0], 12, 2), (49,48,45,255))  # neutral indicator plate inside the frame
        self.assertGreater(at(decoded[1], cx, cy)[0], 100)               # hover brighter
        self.assertGreater(at(decoded[2], cx, cy)[0], at(decoded[1], cx, cy)[0])
        self.assertLess(at(decoded[3], cx, cy)[0], 100)                  # disabled darker

    def test_native_crop_is_pixel_exact_and_never_copies_old_vehicle(self):
        for kind,(width,height) in ICON_KINDS.items():
            strip=ICON_STRIPS[kind]
            bases=[bytearray(solid(width,height,(200,v,90,255))) for v in range(4)]
            # An old/oversize client paints everywhere: strip still wins.
            icons=compose(kind,solid(width,height,(10,20,30,255)),bases)
            for variant,icon in enumerate(icons):
                rgba=read_tga(icon)[2]
                for y in (0,height//2,height-1):
                    start=y*width*4
                    self.assertEqual(rgba[start:start+strip*4],bases[variant][start:start+strip*4])
                    self.assertNotEqual(rgba[start+strip*4:start+(strip+1)*4],bases[variant][start+strip*4:start+(strip+1)*4])

    def test_missing_native_states_fall_back_to_available_crop(self):
        w,h=ICON_KINDS['unit_icon']
        base=bytearray(solid(w,h,(100,40,90,255)))
        resources=FakeResources({'interface/scene/unit_icon/hull_00.tga':write_tga(w,h,base)})
        crops=chassis_strips(resources,['unknown','hull'],'unit_icon')
        self.assertEqual(crops[0],base)
        for icon in compose('unit_icon',solid(w,h,(0,0,0,0)),crops):
            self.assertEqual(read_tga(icon)[2][:4],base[:4])

    def test_small_icon_keeps_native_frame_alpha_even_with_opaque_client_render(self):
        width,height=ICON_KINDS['unit_icon_small']
        # The real T-26 plate is x=7..102 within its 112px texture. Variants
        # include translucent borders; preserving just the texture size fails.
        base=bytearray(solid(width,height,(180,80,20,0)))
        for y in range(height):
            for x in range(7,103):base[(y*width+x)*4+3]=128 if x in (7,102) else 255
        for raw in compose('unit_icon_small',solid(width,height,(10,40,90,255)),[base,None,None,None]):
            w,h,rgba=read_tga(raw)
            self.assertEqual((w,h),(112,48))
            self.assertEqual(rgba[3::4],base[3::4])
            self.assertEqual(rgba[0:24*4],base[0:24*4])  # indicator pixels stay native
            self.assertNotEqual(rgba[50*4:50*4+3],base[50*4:50*4+3])  # no old vehicle RGB

    def test_missing_small_native_icon_does_not_create_an_oversize_plate(self):
        w,h=ICON_KINDS['unit_icon_small']
        for raw in compose('unit_icon_small',solid(w,h,(200,100,80,255))):
            rgba=read_tga(raw)[2]
            for y in range(h):
                visible=[x for x in range(w) if rgba[(y*w+x)*4+3]]
                self.assertEqual(visible,list(range(8,104)))

if __name__ == "__main__":
    unittest.main()
