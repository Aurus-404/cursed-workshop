import importlib.util
from pathlib import Path
import runpy
import struct
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("workshop_release", ROOT / "scripts/prepare_workshop_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
builder = runpy.run_path(str(ROOT / "scripts/build-workshop-item.py"))


class ReleasePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def distribution(self):
        dist = self.root / "dist"
        dist.mkdir()
        raw = bytearray(80)
        raw[:2] = b"MZ"
        struct.pack_into("<I", raw, 60, 64)
        raw[64:68] = b"PE\0\0"
        struct.pack_into("<H", raw, 68, 0x8664)
        (dist / "CursedWorkshop.exe").write_bytes(raw)
        for name in ("index.html", "assets/app.js", "assets/style.css"):
            for base in (dist / "_internal/workshop/web", self.root / "workshop/web"):
                path = base / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture", encoding="utf8")
        return dist

    def test_validate_distribution_rejects_stale_ui(self):
        dist = self.distribution()
        release.validate_distribution(dist, self.root)
        (dist / "_internal/workshop/web/assets/app.js").write_text("old", encoding="utf8")
        with self.assertRaisesRegex(ValueError, "Stale"):
            release.validate_distribution(dist, self.root)

    def test_validate_distribution_requires_windows_x64(self):
        dist = self.distribution()
        exe = dist / "CursedWorkshop.exe"
        raw = bytearray(exe.read_bytes())
        struct.pack_into("<H", raw, 68, 0x14c)
        exe.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, "x64"):
            release.validate_distribution(dist, self.root)

    def test_validate_distribution_blocks_local_save_before_copying(self):
        dist = self.distribution()
        (dist / "private.sav").write_text("private")
        with self.assertRaisesRegex(ValueError, "Unexpected data"):
            release.validate_distribution(dist, self.root)

    def test_audit_blocks_private_game_and_debug_files(self):
        for name in ("campaign.sav", "settings.json", "depot.json", "game.log", "game.pak", "tank.mdl", "app.js.map", "secret.pfx"):
            with self.subTest(name=name):
                item = self.root / "item"
                item.mkdir(exist_ok=True)
                path = item / name
                path.write_text("private", encoding="utf8")
                with self.assertRaises(ValueError):
                    release.audit_item(item)
                path.unlink()

    def test_audit_allows_only_python_runtime_zip(self):
        item = self.root / "item"
        runtime = item / "CursedWorkshop/_internal/base_library.zip"
        runtime.parent.mkdir(parents=True)
        runtime.write_bytes(b"fixture runtime")
        report = release.audit_item(item)
        self.assertEqual(report["CursedWorkshop/_internal/base_library.zip"]["sha256"], release.digest(runtime))
        (item / "attachments.zip").write_bytes(b"private")
        with self.assertRaisesRegex(ValueError, "Unexpected archive"):
            release.audit_item(item)

    def test_audit_rejects_development_folder(self):
        path = self.root / "item/operations/backup.txt"
        path.parent.mkdir(parents=True)
        path.write_text("private")
        with self.assertRaisesRegex(ValueError, "workspace folder"):
            release.audit_item(self.root / "item")

    def test_builder_does_not_overwrite_existing_item(self):
        dist = self.distribution()
        item = self.root / "release/CursedWorkshop_item"
        item.mkdir(parents=True)
        (item / "keep.txt").write_text("preserve")
        with self.assertRaises(FileExistsError):
            builder["build_item"](dist, item.parent)
        self.assertEqual((item / "keep.txt").read_text(), "preserve")

    def test_builder_keeps_runtime_and_omits_debug_map(self):
        dist = self.distribution()
        (dist / "_internal/workshop/web/assets/app.js.map").write_text("development source")
        item = builder["build_item"](dist, self.root / "release")
        self.assertTrue((item / "CursedWorkshop/_internal/workshop/web/assets/app.js").is_file())
        self.assertFalse(list(item.rglob("*.map")))
        self.assertLess((item.parent / "preview.png").stat().st_size, 1_000_000)
        self.assertTrue((item / "USER_GUIDE.md").is_file())
        self.assertNotIn("interface is in Spanish;", (item / "README.txt").read_text(encoding="utf8"))

    def test_upload_metadata_is_the_public_wip_release_and_accepts_only_numeric_ids(self):
        text = release.upload_vdf(self.root / "item", self.root / "preview.png")
        self.assertIn('"appid" "400750"', text)
        self.assertIn('"visibility" "0"', text)  # public WIP release authorized 19/10
        self.assertIn('"title" "Cursed Workshop [WIP]"', text)
        self.assertIn('"publishedfileid" "0"', text)
        self.assertIn('"publishedfileid" "12345"', release.upload_vdf(self.root, self.root / "p.png", "12345"))
        with self.assertRaises(ValueError):
            release.upload_vdf(self.root, self.root / "p.png", '1"\n"visibility" "0')

    def test_existing_release_and_assigned_id_are_never_replaced(self):
        out = self.root / "release"
        out.mkdir()
        (out / "upload.vdf").write_text('"publishedfileid" "12345"')
        with self.assertRaises(FileExistsError):
            release.prepare_release(self.root / "missing", out)
        self.assertEqual((out / "upload.vdf").read_text(), '"publishedfileid" "12345"')


if __name__ == "__main__":
    unittest.main()
