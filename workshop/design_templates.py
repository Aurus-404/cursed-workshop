"""Design templates: a saved design applied to another vehicle of the same model.

A template keeps the chassis model and the design recipe (armor, commander MG,
weapons and their donors, mobility, joint limits, crew...). Applying it is done
by the editor, which picks this campaign's own donor units; building still
validates and consumes them like any design.
"""
import json
import threading
import time
from pathlib import Path

from .transactions import _atomic_json

FILE = 'design_templates.json'
TEMPLATES = 200
NAME = 80
SIZE = 900_000  # below the 1 MB request limit
_LOCK = threading.Lock()


def _path(workspace):
    return Path(workspace) / FILE


def _load(workspace):
    try:
        data = json.loads(_path(workspace).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def listing(workspace):
    with _LOCK:
        data = _load(workspace)
    return [{'name': name, **row} for name, row in sorted(data.items(), key=lambda item: item[0].casefold())]


def _name(value):
    name = ' '.join(str(value or '').split()) if isinstance(value, str) else ''
    if not 0 < len(name) <= NAME:
        raise ValueError('Poné un nombre de hasta %d caracteres para la plantilla.' % NAME)
    return name


def save(workspace, body):
    name = _name(body.get('name'))
    chassis, label, transform = body.get('chassis'), body.get('label') or '', body.get('transform')
    if not isinstance(chassis, str) or not chassis or not isinstance(label, str):
        raise ValueError('La plantilla necesita el modelo del chasis.')
    if not isinstance(transform, dict) or not isinstance(transform.get('assembly_draft'), dict):
        raise ValueError('Solo se guardan como plantilla los diseños del editor.')
    if len(json.dumps(transform)) > SIZE:
        raise ValueError('El diseño es demasiado grande para guardarlo como plantilla.')
    with _LOCK:
        data = _load(workspace)
        if name not in data and len(data) >= TEMPLATES:
            raise ValueError('Ya hay %d plantillas: borrá alguna antes de guardar otra.' % TEMPLATES)
        data[name] = {'chassis': chassis, 'label': label[:200], 'transform': transform, 'saved': int(time.time())}
        Path(workspace).mkdir(parents=True, exist_ok=True)
        _atomic_json(_path(workspace), data)
    return listing(workspace)


def delete(workspace, body):
    name = _name(body.get('name'))
    with _LOCK:
        data = _load(workspace)
        if data.pop(name, None) is not None:
            _atomic_json(_path(workspace), data)
    return listing(workspace)
