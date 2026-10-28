"""Read-only evidence that the running game loaded the generated local addon.

An options.set edit is a startup preference, never proof of a live mod load.
The publication fence also prevents a previous activation from approving newly
written vehicles. No game files, processes or campaign saves are changed here.
"""
import hashlib
import os
from pathlib import Path
import re
import time

from .locations import game_log_path

ADDON_NAME = 'Cursed Workshop - Built Vehicles'
LOCAL_MOD_HELP = ('En Extras → Mods, activá «Cursed Workshop - Built Vehicles» (mod local, carpeta modular_workshop) '
                  'y pulsá Aplicar. Ubicalo al final de la lista, después de todos los demás mods. '
                  'La entrada de Steam «Cursed Workshop» es la herramienta y no reemplaza este mod.')
WAIT_MESSAGE = ('No cargues ni guardes la campaña todavía: falta comprobar que el juego cargó el mod de vehículos.')
_MOD = re.compile(r'Mod: (activate|disable) mod ([^\s]+)')
_MOUNT = re.compile(r'Mount package (.+?\.pak) with \d+ entries', re.I)
_PAGE = re.compile(r'GUI: Show page (\w+) with mods \[')
_MISSING_SQUAD = re.compile(r'\bDiscard mw_[^\s:]+: Multiplayer unit not found during campaign load')


def preserve_campaign(workspace, source, staged, expected_hash):
    """Keep the verified assembled save outside prunable operation folders."""
    raw=Path(staged).read_bytes()
    digest=hashlib.sha256(raw).hexdigest()
    if digest != expected_hash:raise ValueError('La partida preparada cambió antes de crear la copia de recuperación.')
    key=hashlib.sha256(str(Path(source).resolve()).encode('utf-8')).hexdigest()
    folder=Path(workspace)/'backups'/'installed_campaigns'/key
    folder.mkdir(parents=True,exist_ok=True)
    destination=folder/(digest+'.sav')
    if destination.exists():
        if destination.read_bytes()!=raw:raise ValueError('La copia de recuperación existente cambió; se conserva sin sobrescribirla.')
    else:
        temporary=destination.with_suffix('.tmp')
        temporary.write_bytes(raw)
        os.replace(temporary,destination)
    return str(destination)


def _path(value):
    value = str(value).replace('\\', '/').rstrip('/').casefold()
    return value[2:] if value.startswith('z:/') else value


def publication_fence(game_root, log=None):
    path = Path(log) if log else game_log_path(game_root)
    result = {'game_root': _path(Path(game_root).resolve()), 'time_ns': time.time_ns()}
    try:
        raw = path.read_bytes()
        result.update(path=str(path), size=len(raw), digest=hashlib.sha256(raw).hexdigest())
    except OSError:
        pass
    return result


def parse_load(raw, game_root, fence=None, *, modified_ns=None):
    """Require a matching installation, final addon activation and completed UI load."""
    if isinstance(raw, str): raw = raw.encode('utf-8')
    minimum = 0
    if fence and fence.get('game_root') == _path(Path(game_root).resolve()):
        if fence.get('pending'):return 'publication_pending'
        size = fence.get('size')
        if size is not None and len(raw) >= size and hashlib.sha256(raw[:size]).hexdigest() == fence.get('digest'):
            minimum = size
        elif modified_ns is None or modified_ns < fence['time_ns']:
            return 'unknown'
    root = _path(Path(game_root) / 'resource') + '/'
    matching, active, completed, last_activation, offset = False, None, False, None, 0
    missing_squad=False
    for line in raw.splitlines(keepends=True):
        text = line.decode('utf-8', errors='replace')
        mount = _MOUNT.search(text)
        if mount and _path(mount[1]).startswith(root): matching = True
        mod = _MOD.search(text)
        if mod:
            completed = False
            missing_squad=False
            identifier = mod[2].casefold().split(':', 1)[0]
            if identifier in ('modular_workshop', 'mod_modular_workshop'):
                active = mod[1] == 'activate'
                if active: last_activation = offset
            if mod[1] == 'activate' and identifier not in ('modular_workshop', 'mod_modular_workshop'):
                # A later addon could override the shared roster; require our addon last.
                last_activation = None
        if _PAGE.search(text): completed = True
        if 'Loading Campaign:' in text:missing_squad=False
        if _MISSING_SQUAD.search(text):missing_squad=True
        offset += len(line)
    if not matching: return 'unknown'
    if active is False: return 'missing'
    if active is None: return 'unknown'
    if missing_squad:return 'roster_missing'
    if last_activation is None or last_activation < minimum or not completed: return 'reload_required'
    return 'loaded'


def readiness(game_root, game, *, installed, required=True, fence=None, log=None):
    if not required: return {'required': False, 'status': 'not_required', 'ready': False, 'message': ''}
    status = 'missing' if not installed else 'waiting_for_game' if not game.get('running') else None
    if fence and fence.get('pending') and fence.get('game_root') == _path(Path(game_root).resolve()):
        status='publication_pending'
    if status is None:
        path = Path(log) if log else game_log_path(game_root)
        try:
            status = parse_load(path.read_bytes(), game_root, fence, modified_ns=path.stat().st_mtime_ns)
        except OSError:
            status = 'unknown'
    message = ('El juego cargó el mod local de vehículos después de la última instalación.' if status == 'loaded'
               else WAIT_MESSAGE + '\n' + LOCAL_MOD_HELP)
    if status == 'waiting_for_game':
        message = 'Vehículo guardado. Abrí el juego y comprobá aquí que cargó el mod local antes de abrir la campaña.\n' + LOCAL_MOD_HELP
    if status == 'reload_required':
        message += '\nSi ya estaba activo, desactivalo y aplicá; después volvé a activarlo y aplicá.'
    if status == 'publication_pending':
        message='La instalación todavía no se confirmó. Terminá o repetí la instalación en el taller antes de cargar la campaña.'
    if status == 'roster_missing':
        message=('El juego descartó unidades del taller al cargar la campaña porque no encontró sus registros. '
                 'No guardes la partida con unidades ausentes. Corregí la carga del mod local; si ya guardaste, '
                 'usá la copia de recuperación correspondiente a esa campaña.\n'+LOCAL_MOD_HELP)
    return {'required': True, 'status': status, 'ready': status == 'loaded', 'message': message}
