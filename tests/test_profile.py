import tempfile
import unittest
from pathlib import Path

from workshop.profile import activate_addon, active_mods, profile_for_save

OPTIONS = ('{options\r\n\t{video\r\n\t\t{adapter "GPU"}\r\n\t}\r\n\t{mods\r\n\t\t"mod_2905667604:0"\r\n'
           '\t\t"modular_workshop_293531d5a5:0"\r\n\t}\r\n}\r\n')


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "campaign").mkdir()
        self.options = self.root / "options.set"
        self.options.write_bytes(OPTIONS.encode())

    def tearDown(self):
        self.temp.cleanup()

    def test_shared_addon_last_required_mods_added_legacy_removed_with_backup(self):
        result = activate_addon(self.options, ["mod_2905667604:0", "mod_123:0"], self.root / "backup")
        self.assertEqual(active_mods(self.options), ["mod_2905667604:0", "mod_123:0", "modular_workshop:0"])
        self.assertEqual(result["deactivated"], ["modular_workshop_293531d5a5:0"])
        self.assertEqual(Path(result["backup"]).read_bytes(), OPTIONS.encode())
        text = self.options.read_bytes()
        self.assertIn(b'{adapter "GPU"}', text)
        self.assertNotIn(b"\n\t\t\"mod_123", text.replace(b"\r\n", b"\r\r"))  # line endings stay CRLF
        again = activate_addon(self.options, ["mod_2905667604:0"], self.root / "backup")
        self.assertFalse(again["changed"])

    def test_profile_is_found_from_campaign_save(self):
        self.assertEqual(profile_for_save(self.root / "campaign" / "x.sav"), self.options.resolve())


if __name__ == "__main__":
    unittest.main()


class PlannedOrderTests(unittest.TestCase):
    def test_resources_follow_the_order_the_game_mounts(self):
        # options.set had MACE active; the campaign lists another mod first.
        # The game mounts MACE first; reading the campaign order instead copied
        # the wrong entity.set and the game closed ("define not found").
        import tempfile
        from pathlib import Path
        from workshop.profile import planned_mod_order
        with tempfile.TemporaryDirectory() as temp:
            options = Path(temp) / "options.set"
            options.write_text('{options {mods "mod_2905667604:0" "modular_workshop:0"}}', encoding="utf-8")
            order = planned_mod_order(options, ["mod_2926397026:0", "mod_2905667604:0", "mod_3792487101:0", "modular_workshop:0"])
            self.assertEqual(order, ["mod_2905667604:0", "mod_2926397026:0", "mod_3792487101:0", "modular_workshop:0"])
            self.assertEqual(planned_mod_order(None, ["b:0", "a:0"]), ["b:0", "a:0", "modular_workshop:0"])
