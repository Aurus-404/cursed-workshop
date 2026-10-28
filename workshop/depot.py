"""Leftover chassis and turrets, kept by the workshop per campaign save.

Building a vehicle leaves parts over: the chassis' own turret and the donor's
chassis. They are recorded here (not in the game) so a later build can use
them, e.g. a Pz IV's chassis in one vehicle and its turret in another.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import secrets

PARTS = ("hull", "turret")


class DepotError(ValueError):
    pass


class Depot:
    def __init__(self, workspace: str | Path):
        self.path = Path(workspace) / "depot.json"

    @staticmethod
    def key(save_path: str | Path) -> str:
        return str(Path(save_path).resolve()).casefold()

    def _load(self) -> dict:
        if not self.path.is_file():
            return {"schema": 1, "campaigns": {}}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data.setdefault("campaigns", {})
        return data

    def _store(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def items(self, save_path: str | Path) -> list[dict]:
        return list(self._load()["campaigns"].get(self.key(save_path), []))

    def get(self, save_path: str | Path, item_id: str) -> dict:
        item = next((row for row in self.items(save_path) if row["id"] == item_id), None)
        if item is None:
            raise DepotError("Esa pieza ya no está en el depósito de esta partida.")
        return item

    def apply(self, save_path: str | Path, add: list[dict], take: list[str]) -> list[dict]:
        """Remove used parts and add leftovers in one write."""
        data = self._load()
        rows = data["campaigns"].setdefault(self.key(save_path), [])
        missing = [item_id for item_id in take if item_id not in {row["id"] for row in rows}]
        if missing:
            raise DepotError("Una pieza del depósito ya fue usada; volvé a abrir la campaña.")
        rows[:] = [row for row in rows if row["id"] not in set(take)]
        for item in add:
            if item.get("part") not in PARTS or not item.get("template"):
                raise DepotError("Pieza de depósito inválida")
            mount = item.get("mount")
            if mount is not None and (not isinstance(mount, str) or not mount or len(mount) > 160):
                raise DepotError("Pieza de depósito inválida")
            rows.append({"id": secrets.token_hex(5), "part": item["part"], "template": item["template"],
                         "label": item.get("label") or item["template"], "source": item.get("source", ""),
                         **({"mount": mount} if mount else {}),
                         "created": datetime.now().isoformat(timespec="seconds")})
        self._store(data)
        return list(rows)
