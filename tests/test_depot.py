import tempfile
import unittest
from pathlib import Path

from workshop.depot import Depot, DepotError


class DepotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.save = self.root / "campaign" / "Mi Partida.sav"
        self.depot = Depot(self.root / "workspace")

    def tearDown(self):
        self.temp.cleanup()

    def test_leftovers_are_kept_per_save_and_taken_once(self):
        rows = self.depot.apply(self.save, [{"part": "turret", "template": "panzer4h", "label": "Torreta de Pz IV"},
                                            {"part": "hull", "template": "sdkfz222a", "label": "Chasis de 222"}], [])
        self.assertEqual([row["part"] for row in rows], ["turret", "hull"])
        self.assertEqual(self.depot.items(self.root / "campaign" / "otra.sav"), [])
        turret = rows[0]["id"]
        self.assertEqual(self.depot.get(self.save, turret)["template"], "panzer4h")
        self.depot.apply(self.save, [], [turret])
        with self.assertRaises(DepotError):
            self.depot.get(self.save, turret)
        with self.assertRaises(DepotError):  # a part cannot be used twice
            self.depot.apply(self.save, [], [turret])
        self.assertEqual([row["template"] for row in Depot(self.root / "workspace").items(self.save)], ["sdkfz222a"])

    def test_invalid_parts_are_rejected(self):
        with self.assertRaises(DepotError):
            self.depot.apply(self.save, [{"part": "wheel", "template": "x"}], [])


if __name__ == "__main__":
    unittest.main()
