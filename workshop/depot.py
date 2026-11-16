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
import re

PARTS = ("hull", "turret", "gun")


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
        self.recover(save_path)
        return list(self._load()["campaigns"].get(self.key(save_path), []))

    def stage(self,save_path,operation_id,transaction,parts):
        data=self._load()
        data.setdefault('pending',{})[operation_id]={'save':self.key(save_path),'journal':str(transaction['journal_path']),'parts':parts}
        self._store(data)

    def recover(self,save_path):
        from .transactions import _read_journal
        for identifier,entry in list(self._load().get('pending',{}).items()):
            if entry['save']!=self.key(save_path):continue
            try:_,journal=_read_journal(entry['journal'])
            except (OSError,ValueError):continue
            if journal['state']!='committed':continue
            self.apply(save_path,entry['parts'],[],operation_id=identifier)
            data=self._load();data.get('pending',{}).pop(identifier,None);self._store(data)

    def get(self, save_path: str | Path, item_id: str) -> dict:
        item = next((row for row in self.items(save_path) if row["id"] == item_id), None)
        if item is None:
            raise DepotError("Esa pieza ya no está en el depósito de esta partida.")
        return item

    def apply(self, save_path: str | Path, add: list[dict], take: list[str], *, operation_id=None) -> list[dict]:
        """Remove used parts and add leftovers in one write."""
        data = self._load()
        rows = data["campaigns"].setdefault(self.key(save_path), [])
        if operation_id and operation_id in data.get('completed',[]):return list(rows)
        missing = [item_id for item_id in take if item_id not in {row["id"] for row in rows}]
        if missing:
            raise DepotError("Una pieza del depósito ya fue usada; volvé a abrir la campaña.")
        rows[:] = [row for row in rows if row["id"] not in set(take)]
        for item in add:
            if item.get("part") not in PARTS or not item.get("template"):
                raise DepotError("Pieza de depósito inválida")
            if item['part']=='gun' and (not isinstance(item.get('place'),str) or not re.fullmatch(r'[A-Za-z0-9_@.-]{1,80}',item['place'])):
                raise DepotError('Pieza de depósito inválida')
            mount = item.get("mount")
            if mount is not None and (not isinstance(mount, str) or not mount or len(mount) > 160):
                raise DepotError("Pieza de depósito inválida")
            rows.append({"id": secrets.token_hex(5), "part": item["part"], "template": item["template"],
                         "label": item.get("label") or item["template"], "source": item.get("source", ""),
                         **({"mount": mount} if mount else {}),
                         **{key:item[key] for key in ('place','weapon','nation','origin','origin_template','definition_path','component_type') if key in item},
                         "created": datetime.now().isoformat(timespec="seconds")})
        if operation_id:data.setdefault('completed',[]).append(operation_id)
        self._store(data)
        return list(rows)


def attributes(index,item,addon):
    """Resolve provenance of old stock through its actual assembly recipes."""
    row=dict(item);template=row.get('origin_template') or row['template'];seen=set()
    while index is not None and template not in seen:
        seen.add(template)
        try:info=index.resolve_entity(template)
        except (KeyError,OSError,ValueError):break
        match=re.match(r'entity/-vehicle/\+?([^/]+)/',info['definition_path'])
        nation=match.group(1) if match else ''
        if nation and nation!='workshop':
            row.setdefault('nation',nation)
            source=next((s for s in index.sources if s['id']==info.get('source_id')), {})
            row.setdefault('origin',source.get('name') or info.get('source_id') or '')
            break
        if not re.fullmatch(r'mw_[A-Za-z0-9_]+',template):break
        try:
            value=json.loads((Path(addon)/('assembly_'+template+'.json')).read_text(encoding='utf8'))
            metadata=value.get('metadata',value)
            template=metadata.get('recipient' if row['part']=='hull' else 'donor')
            if not isinstance(template,str):break
        except (KeyError,OSError,ValueError):break
    return row
