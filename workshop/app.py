"""Local GUI server; all game/save access stays on this computer."""
from __future__ import annotations
import argparse
import base64
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import mimetypes
import os
import platform
import re
from pathlib import Path
import secrets
import shutil
import struct
import tempfile
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote
import webbrowser
import zlib

from . import __version__
from .process_guard import game_state, require_game_closed, require_game_idle, running_game_processes
from .locations import GAME_APP_ID, config_dirs, documents_dir, game_log_path, running_under_wine, unix_path
from .registration import ADDON_ID, ADDON_MOD, is_own_mod
from .profile import activate_addon, profile_for_save
from .depot import Depot
from .transactions import long_path
from .diagnostics import Diagnostics

GAME_FOLDER = 'Call to Arms - Gates of Hell'
WEB_ROOT = Path(__file__).parent / 'web'

def app_directory() -> Path:
    return Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent.parent

def is_game_root(path) -> bool:
    return bool(path) and (Path(path)/'resource'/'gamelogic.pak').is_file()

def steam_libraries() -> list[Path]:
    """The Steam install (registry) and every library in its libraryfolders.vdf."""
    installs=[]
    if os.name=='nt':
        import winreg
        for hive,key,value in ((winreg.HKEY_CURRENT_USER,r'Software\Valve\Steam','SteamPath'),
                               (winreg.HKEY_LOCAL_MACHINE,r'SOFTWARE\WOW6432Node\Valve\Steam','InstallPath'),
                               (winreg.HKEY_LOCAL_MACHINE,r'SOFTWARE\Valve\Steam','InstallPath')):
            try:
                with winreg.OpenKey(hive,key) as handle: installs.append(Path(winreg.QueryValueEx(handle,value)[0]))
            except OSError: pass
    libraries=[]
    for install in installs:
        libraries.append(install)
        try: text=(install/'steamapps'/'libraryfolders.vdf').read_text(encoding='utf-8',errors='replace')
        except OSError: continue
        libraries+=[Path(path.replace('\\\\','\\')) for path in re.findall(r'"path"\s+"([^"]+)"',text)]
    return list(dict.fromkeys(libraries))

def find_game_root(start=None) -> str:
    """The game folder on any PC: the library this program was installed into
    from the Workshop (steamapps/workshop/content/400750/<item>), else any
    Steam library that has the game. Empty when it cannot be found."""
    steamapps=[]
    for parent in Path(start or app_directory()).resolve().parents:
        if parent.name.casefold()=='steamapps':
            steamapps.append(parent); break
    steamapps+=[library/'steamapps' for library in steam_libraries()]
    for folder in dict.fromkeys(steamapps):
        names=[GAME_FOLDER]
        try:
            manifest=(folder/f'appmanifest_{GAME_APP_ID}.acf').read_text(encoding='utf-8',errors='replace')
            names.insert(0,re.search(r'"installdir"\s+"([^"]+)"',manifest).group(1))
        except (OSError,AttributeError): pass
        for name in names:
            if is_game_root(folder/'common'/name): return str((folder/'common'/name).resolve())
    return ''

GAME_NOT_FOUND=('No se encontró la carpeta del juego. Abrí Ajustes y elegí la carpeta «Call to Arms - Gates of Hell» '
                '(la que contiene resource y binaries, en steamapps\\common), no la carpeta de esta herramienta.')

def default_workspace() -> Path:
    """Operation journals and backups must outlive the program folder, which a
    rebuild or update replaces wholesale; keep them in the user's profile."""
    if getattr(sys,'frozen',False) and os.environ.get('LOCALAPPDATA'):
        return Path(os.environ['LOCALAPPDATA'])/'ModularWorkshop'
    return app_directory()/'data'

def discover_saves(game_root=None) -> list[dict]:
    # The folder the game uses now comes first; a same-named save left in an
    # older location is the stale copy and is not offered.
    roots=[folder/'profiles' for folder in config_dirs(game_root)]
    rows,seen,names=[],set(),set()
    for root in roots:
        try:
            saves=list(root.glob('*/campaign/**/*.sav')) if root.is_dir() else []
        except OSError: continue
        for save in saves:
            key=os.path.normcase(os.path.realpath(save))
            try:
                if key in seen or not save.is_file(): continue
                stat=save.stat()
            except OSError: continue
            name=(save.parent.parent.name.casefold(),save.name.casefold())
            if name in names: continue
            seen.add(key); names.add(name); rows.append({'path':str(save),'name':save.stem,'size':stat.st_size,'modified':stat.st_mtime})
    return sorted(rows,key=lambda x:x['modified'],reverse=True)

def serial(value):
    if isinstance(value,Path): return str(value)
    if hasattr(value,'to_dict'): return value.to_dict()
    if hasattr(value,'as_dict'): return value.as_dict()
    if isinstance(value,dict): return {str(k):serial(v) for k,v in value.items() if not isinstance(v,bytes)}
    if isinstance(value,(list,tuple,set)): return [serial(v) for v in value]
    return value

FRIENDLY_ERRORS=[
    ('Game resource folder missing',GAME_NOT_FOUND),
    ('This Conquest has no units yet',
     'Esta Conquista todavía no tiene unidades: se guardó antes de comprar o jugar nada. En el juego, comprá unidades '
     'o jugá una batalla, guardá y volvé a abrirla en la herramienta.'),
    ('CampaignSquads block',
     'La herramienta no reconoce esta partida como una Conquista: no encontró su lista de escuadras. Puede ser una partida '
     'de otro modo (misión o escaramuza), una conquista recién creada o de un mod que cambia el formato. Probá con otra '
     'partida de Conquista y mandá este archivo .sav al Discord del mod para revisarlo.'),
    ('Missing chassis assembly recipe',
     'Falta la receta de un vehículo del taller. Revisá las copias de seguridad del addon.'),
    ('Cyclic chassis assembly recipe',
     'Las recetas del vehículo forman una cadena circular. Revisá las copias de seguridad del addon.'),
    ('Missing chassis provenance',
     'La receta del vehículo no identifica su chasis original. Revisá las copias de seguridad del addon.'),
    ('require a recognized or explicitly selected weapon mount',
     'No se encontró dónde montar el módulo. En "Punto de montaje" del chasis elegí una opción "Libre" (sobre la cubierta, '
     'la torreta o una escotilla), o elegí a mano la parte del módulo que se traslada.'),
    ('has no resolved weapon place',
     'El vehículo elegido como torreta no tiene un arma que se pueda trasladar (por ejemplo, un acoplado de munición, '
     'un camión de transporte o un vehículo de demolición). Elegí otro vehículo como torreta.'),
    ('Loose resource changed after indexing',
     'Los archivos del juego o de los mods cambiaron mientras la herramienta estaba abierta. Volvé a abrir la campaña con el botón "Abrir" y probá de nuevo.'),
    ('Source changed after compilation',
     'Un mod cambió (por ejemplo, Steam actualizó MACE) después de crear el vehículo. Creá el vehículo de nuevo antes de instalar.'),
    ('Active source order changed after compilation',
     'Cambió la lista de mods después de crear el vehículo. Creá el vehículo de nuevo antes de instalar.'),
    ('Weapon ownership requires a profile for places',
     'Esta torreta comparte armas con el casco de una forma que la herramienta todavía no sabe separar. Probá con otro vehículo como módulo.'),
    ('Save-copy destination already exists',
     'Ya existe una copia de campaña con ese nombre. Abrí la copia más reciente y seguí desde ella.'),
]

# Windows errors of OneDrive's online-only files (cloud provider missing,
# unavailable or not syncing). Python reports unmapped ones as errno 22.
CLOUD_FILE_ERRORS={362,388,389,390,391,392,393,394,395,396,397,398,399,400,401,402,403,404}

def unreadable_save(error) -> str:
    if getattr(error,'winerror',None) in CLOUD_FILE_ERRORS or getattr(error,'errno',None)==22:
        return ('No se pudo leer la partida: está guardada solo en OneDrive (en la nube) y no se pudo descargar. '
                'En el Explorador de archivos, hacé clic derecho sobre la partida → «Mantener siempre en este dispositivo», '
                'o revisá que OneDrive esté funcionando, y abrila de nuevo.')
    return 'No se pudo leer la partida: '+str(error)

def friendly_error(exc) -> str:
    """Spanish explanation for known failures; the technical text stays below it."""
    text=str(exc)
    for pattern,message in FRIENDLY_ERRORS:
        if pattern.casefold() in text.casefold():
            return message+'\n\nDetalle técnico: '+text
    if isinstance(exc,(PermissionError,)) or text.startswith(('Cerrá','Abrí','Elegí','Seleccioná','Generá','Faltan','Esta partida','El donante','No se encontró la carpeta del juego','No se pudo leer la partida')):
        return text
    return ('La operación no se pudo completar. Usá «Copiar diagnóstico» para compartir el informe como texto.'
            '\n\nDetalle técnico: '+text)

class AppState:
    def __init__(self,workspace:Path):
        self.workspace=workspace.resolve(); self.workspace.mkdir(parents=True,exist_ok=True)
        self.config_path=self.workspace/'settings.json'
        self.settings={'game_root':'', 'mod_roots':[]}
        if self.config_path.is_file():
            self.settings.update(json.loads(self.config_path.read_text(encoding='utf-8')))
        if not is_game_root(self.settings.get('game_root')):
            self.settings['game_root']=find_game_root()
        self.token=secrets.token_urlsafe(32)
        self.index=None; self.snapshot=None; self.previews={}; self.jobs={}; self.lock=threading.RLock()
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='workshop')
        self.last_result=None
        self.portrait_previews={}
        self.diagnostics=Diagnostics()
        self.latest_jobs={}
        self.preview_futures={}
        self.foreground=0
        self.storage_lock=threading.Lock()

    def record_error(self, exc, operation):
        with self.lock:
            self.diagnostics.record(exc, operation, (self.token, str(Path.home())))

    def diagnostic(self):
        with self.lock:
            return self.diagnostics.snapshot(self.index, self.snapshot is not None, (self.token, str(Path.home())))

    @contextmanager
    def phase(self,label):
        started=time.perf_counter();state='done'
        try:yield
        except Exception:
            state='error'
            raise
        finally:
            elapsed=(time.perf_counter()-started)*1000
            with self.lock:self.diagnostics.record_phase(label,state,elapsed,(self.token,str(Path.home())))
            logging.info('Phase timing: %s | %s | elapsed_ms=%.0f',label,state,elapsed)

    def closed(self): return require_game_idle(self.settings.get('game_root') or None)

    def addon_root(self) -> Path:
        return Path(self.settings['game_root'])/'mods'/ADDON_ID

    def save_settings(self):
        with self.lock:
            tmp=self.config_path.with_suffix('.tmp')
            tmp.write_text(json.dumps(self.settings,ensure_ascii=False,indent=2),encoding='utf-8'); os.replace(tmp,self.config_path)

    @staticmethod
    def system_language():
        """Windows display language mapped to the interface languages: Spanish and
        Chinese as such, anything else English. Used only until the player picks one."""
        try:
            import ctypes
            primary=ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3ff
        except (AttributeError, OSError):
            return None
        return 'es' if primary==0x0a else 'zh-CN' if primary==0x04 else 'en'

    def ui_preferences(self, body=None):
        # Same workspace settings as game configuration; independent of HTTP port.
        choices={'language':('es','en','zh-CN'),
                 'theme':('graphite','paper','olive','earth','contrast','classic'),
                 'size':('normal','large')}
        booleans={'motion','help','sound','maximized'}
        def valid(key,value):
            return (isinstance(value,str) and value in choices[key]) if key in choices else key in booleans and type(value) is bool
        with self.lock:
            saved=self.settings.get('ui_preferences',{})
            current={k:v for k,v in saved.items() if valid(k,v)} if isinstance(saved,dict) else {}
            if body is not None:
                if not isinstance(body,dict) or any(not valid(k,v) for k,v in body.items()):
                    raise ValueError('Invalid interface preferences.')
                previous=self.settings.copy()
                self.settings['ui_preferences']={**current,**body}
                try:self.save_settings()
                except Exception:
                    self.settings=previous
                    raise
                current=self.settings['ui_preferences']
            result=dict(current)
        if body is None and 'language' not in result:
            system=self.system_language()
            if system:result['system_language']=system
        return result

    def job(self,label,operation,*,lane=None,cancellable=False):
        from .cancellation import current_cancel, current_progress, checkpoint, JobCancelled
        cancellable = bool(cancellable or lane)
        key=secrets.token_hex(8)
        cancel=threading.Event()
        queued_at=time.perf_counter()
        with self.lock:
            self.jobs[key]={'id':key,'label':label,'status':'queued','started':time.time(),'cancellable':cancellable}
            controls=self.__dict__.setdefault('job_controls',{})
            controls[key]=cancel
            if lane:
                previous=self.preview_futures.get(lane)
                if previous:
                    controls[previous[0]].set()
                    if previous[1].cancel():
                        self.jobs[previous[0]].update(status='superseded',finished=time.time())
                self.latest_jobs[lane]=key
        def work():
            running_at=time.perf_counter()
            context=current_cancel.set(cancel if cancellable else None)
            def progress(completed,total):
                with self.lock:self.jobs[key]['progress']={'completed':completed,'total':total}
            progress_context=current_progress.set(progress)
            with self.lock:
                if lane and self.latest_jobs.get(lane)!=key:
                    self.jobs[key].update(status='superseded',finished=time.time()); current_cancel.reset(context); current_progress.reset(progress_context); return
                self.jobs[key]['status']='running'
                self.foreground+=1
            try:
                checkpoint()
                result=serial(operation())
                checkpoint()
                with self.lock: self.jobs[key].update(status='done',result=result,finished=time.time())
            except JobCancelled:
                with self.lock:self.jobs[key].update(status='cancelled',finished=time.time())
            except Exception as exc:
                logging.exception('Operation failed: %s',label)
                self.record_error(exc,label)
                with self.lock: self.jobs[key].update(status='error',error=friendly_error(exc),detail=str(exc),error_type=type(exc).__name__,finished=time.time())
            finally:
                with self.lock:self.foreground-=1
                current_cancel.reset(context)
                current_progress.reset(progress_context)
                elapsed_ms=(time.perf_counter()-running_at)*1000
                queued_ms=(running_at-queued_at)*1000
                with self.lock:
                    status=self.jobs[key]['status']
                    self.diagnostics.record_operation(label,status,elapsed_ms,queued_ms,(self.token,str(Path.home())))
                logging.info('Operation timing: %s | %s | elapsed_ms=%.0f | queued_ms=%.0f',label,status,elapsed_ms,queued_ms)
        with self.lock:
            future=self.pool.submit(work)
            self.__dict__.setdefault('job_futures',{})[key]=future
            if lane:self.preview_futures[lane]=(key,future)
        return {'job_id':key}

    def cancel_job(self, body):
        key=body.get('id')
        with self.lock:
            job=self.jobs.get(key)
            if job is None:raise ValueError('Unknown operation')
            if not job.get('cancellable'):return {'accepted':False,'reason':'Publication cannot be interrupted'}
            if job['status'] not in ('queued','running'):return {'accepted':False,'status':job['status']}
            self.job_controls[key].set()
            future=self.__dict__.get('job_futures',{}).get(key)
            if future is not None and future.cancel():
                job.update(status='cancelled',finished=time.time())
                self.diagnostics.record_operation(job['label'],'cancelled',0,(time.time()-job['started'])*1000)
            return {'accepted':True}

    def unit_image(self, template):
        """Artwork from the effective local stack, never arbitrary user paths."""
        if self.index is None:raise ValueError('Read resources first')
        if not re.fullmatch(r'[A-Za-z0-9_().-]{1,96}',template):raise ValueError('Invalid unit image identifier')
        self.index.resolve_entity(template)
        base=re.sub(r'\([^()]*\)$','',template)
        signature=tuple((s.get('id'),s.get('fingerprint')) for s in self.index.sources)
        cached=getattr(self.index,'_unit_art_lookup',None)
        if cached is None or cached[0]!=signature:
            lookup={}
            for path in self.index.entries:
                if path.startswith('interface/scene/') and path.endswith(('_00.png','_00.tga')):
                    name=path.rsplit('/',1)[-1].rsplit('_00.',1)[0]
                    key=(path.rsplit('/',1)[0],re.sub(r'\([^()]*\)$','',name))
                    lookup.setdefault(key,[]).append(path)
            self.index._unit_art_lookup=(signature,lookup)
        lookup=self.index._unit_art_lookup[1]
        for kind,suffix in (('unit_icon','tga'),('unit_icon_small','tga'),('portrait_squad','png')):
            prefix='interface/scene/'+kind+'/'
            candidates=[prefix+name+'_00.'+suffix for name in dict.fromkeys((template,base))]
            matches=lookup.get((prefix[:-1],base.casefold()),[])
            if len(matches)==1:candidates+=matches
            for path in candidates:
                if not self.index.exists(path):continue
                raw=self.index.read_bytes(path,max_bytes=2*1024*1024)
                if suffix=='png':
                    if raw[:8]!=b'\x89PNG\r\n\x1a\n':continue
                    return raw
                from .icons import read_tga, IconError
                from .textures import png
                try:w,h,rgba=read_tga(raw)
                except IconError:continue
                return png(w,h,rgba)
        raise FileNotFoundError('No unit artwork available')

    def config(self):
        from .resources import discover_installed_mods
        game=self.settings.get('game_root')
        return {'version':__version__,'settings':self.settings,'workspace':str(self.workspace),
                'installed_mods':discover_installed_mods(game) if game and Path(game).is_dir() else [],
                'saves':discover_saves(game or None),'game_processes':running_game_processes(game or None),'game':game_state(game or None),'linux':running_under_wine(),
                'indexed':self.index is not None,'campaign_loaded':self.snapshot is not None,
                'addon_readiness':self.addon_readiness()}

    def mark_addon_published(self, recovery_backup=None, *, pending=False):
        from .addon_readiness import publication_fence
        previous=self.settings.get('addon_publication') or {}
        fence=publication_fence(self.settings['game_root'])
        backup=recovery_backup or previous.get('recovery_backup')
        if backup:fence['recovery_backup']=backup
        if pending:fence['pending']=True
        self.settings['addon_publication']=fence
        self.save_settings()

    def addon_readiness(self, game=None):
        from .addon_readiness import readiness
        root=self.settings.get('game_root')
        if not root:return {'required':False,'ready':False,'status':'not_required','message':''}
        addon=self.addon_root()
        present=(addon/'mod.info').is_file()
        installed=present and all((addon/'resource'/p).is_file() for p in self.GLOBAL_FILES)
        required=present or bool(self.snapshot and any(is_own_mod(m) for m in self.snapshot.to_dict().get('mods',[])))
        fence=self.settings.get('addon_publication') or {}
        result=readiness(root,game if game is not None else game_state(root),installed=installed,
                         required=required,fence=fence)
        if fence.get('game_root')==str(Path(root).resolve()).replace('\\','/').rstrip('/').casefold():
            result['recovery_backup']=fence.get('recovery_backup')
        return result

    def prepare_addon_activation(self, source, game):
        # The live game owns options.set and can overwrite external edits.
        if game.get('running'):return None
        options=profile_for_save(source)
        if options:
            mods=self.snapshot.to_dict().get('mods',[]) if self.snapshot else []
            return activate_addon(options,[m for m in mods if not is_own_mod(m)],
                                  self.workspace/'backups'/'profile')
        return None

    def configure(self,body):
        self.closed()
        game=Path(body.get('game_root',self.settings.get('game_root',''))).resolve()
        if not is_game_root(game):
            raise ValueError('Seleccioná la carpeta del juego que contiene resource y binaries.')
        roots=[str(Path(p).resolve()) for p in body.get('mod_roots',[])]
        if any(not (Path(root)/'mod.info').is_file() for root in roots):
            raise ValueError('Uno de los mods seleccionados no contiene mod.info.')
        changed=str(game)!=self.settings.get('game_root') or roots!=self.settings.get('mod_roots',[])
        if changed:
            self.snapshot=None; self.last_result=None; self.unresolved_mods=[]
            self.clear_portrait_previews()
        self.settings.update(game_root=str(game),mod_roots=roots); self.save_settings()
        result=self.index_resources(); result['campaign_invalidated']=changed
        return result

    def index_resources(self):
        from .resources import ResourceIndex
        if not is_game_root(self.settings.get('game_root')): raise ValueError(GAME_NOT_FOUND)
        self.closed()
        index=ResourceIndex(self.settings['game_root'],self.settings['mod_roots']).build()
        # Previews contain independent overlays and parsed contracts from the
        # old source snapshot. They must not survive opening/reindexing sources.
        self.clear_portrait_previews()
        with self.lock:
            previous=self.index; self.index=index; self.previews.clear(); self.__dict__.pop("checks",None); self.__dict__.pop("crew_models",None)
        self.warm_catalogs()
        if previous: previous.close()
        return {'stats':index.stats,'catalog_count':len(index.list_units())}

    def load_save(self,body):
        from .campaign import load_campaign
        from .resources import discover_installed_mods
        self.closed()
        path=Path(body['path']).resolve()
        if path.suffix.casefold()!='.sav': raise ValueError('Elegí un archivo de campaña .sav.')
        try:
            snapshot=load_campaign(path)
        except OSError as error:
            raise ValueError(unreadable_save(error)) from error
        repaired=self.repair_save_syntax(snapshot)
        if repaired: snapshot=load_campaign(path)
        released=self.release_orphan_guns(snapshot)
        if released: snapshot=load_campaign(path)
        self.last_result=None
        self.clear_portrait_previews()
        mods=snapshot.to_dict().get('mods',[])
        legacy=[mod for mod in mods if is_own_mod(mod) and mod.casefold()!=ADDON_MOD]
        if legacy:
            raise ValueError('Esta partida usa un mod de una versión anterior del taller ('+', '.join(legacy)+
                             '). Generá de nuevo desde la partida original: ahora todos los vehículos van en un único mod.')
        installed=discover_installed_mods(self.settings['game_root'])
        def installed_root(original):
            raw=str(original).rsplit(':',1)[0]
            variants={raw.casefold(),raw.removeprefix('mod_').casefold(),raw.removeprefix('mod:').casefold()}
            matches=[m for m in installed if m['id'].casefold() in variants or m['name'].casefold() in variants]
            return matches[0]['root'] if len(matches)==1 else None
        missing=[mod for mod in mods if not is_own_mod(mod) and installed_root(mod) is None]
        # Read resources in the order the game will mount them: the order the
        # profile's options.set gets on install (mods already active first,
        # also those the campaign does not list), not the campaign's list order.
        try:
            from .profile import planned_mod_order
            order=planned_mod_order(profile_for_save(path),mods)
        except Exception:
            order=list(mods)
        roots=[]
        for original in order:
            if is_own_mod(original): continue  # the shared addon is appended last below
            root=installed_root(original)
            if root and root not in roots: roots.append(root)
        shared=self.addon_root()
        if (shared/'mod.info').is_file():
            roots.append(str(shared))  # earlier variants stay usable as chassis or module
        if roots!=self.settings['mod_roots']:
            self.settings['mod_roots']=roots; self.save_settings(); self.index_resources()
        elif self.index is None: self.index_resources()
        try:fitted=self.reconcile_crews(snapshot)
        except Exception as error:  # the game open, a read-only folder: the campaign still opens
            logging.warning('Crews not fitted to their seats in %s: %s',path,error);fitted=[]
        if fitted:
            snapshot=load_campaign(path);self.index_resources()  # the addon gained reserve squads
        with self.lock: self.snapshot=snapshot
        data=snapshot.to_dict(); data['unresolved_mods']=missing
        for unit in data.get('units',[]):
            template=re.sub(r'\([^()]*\)$','',str(unit.get('template') or ''))
            if unit.get('kind')=='human':
                unit['label']=template.rsplit('/',1)[-1].replace('_',' ').capitalize()
            elif template:
                unit['label']=self.index.label_for(template)
        data['active_mod_roots']=self.settings['mod_roots']
        # Mods the game itself switches off (game version outside their range):
        # the tool ignores them too, so nothing built depends on their files.
        data['disabled_mods']=[{'name':m['name'],'reason':m['reason']} for m in getattr(self.index,'disabled_mods',[])]
        data['mod_stack_problem']=self.mod_stack_problem()
        data['repaired_save']=repaired
        data['crew_fitted']=fitted
        self.unresolved_mods=missing
        with self.lock: self.campaign_view=data
        return {'campaign':self.campaign_data(),'stats':self.index.stats}

    def repair_save_syntax(self,snapshot):
        """Fix, on open, saves an earlier version of the tool left unreadable
        (a designed vehicle's inventory with a quoted id: the game closes while
        loading the campaign). The original is kept in backups/saves first."""
        from .campaign import build_save_copy, repair_inventory_syntax
        from .transactions import assert_game_closed, replace_file_verified
        if getattr(snapshot,'_scene',None) is None: return None
        replacements,repaired=repair_inventory_syntax(snapshot)
        if not replacements: return None
        assert_game_closed()
        self.make_room(snapshot.source_path)
        backup=self.workspace/'backups'/'saves'/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+snapshot.source_path.name)
        replace_file_verified(snapshot.source_path,snapshot.source_hash,build_save_copy(snapshot,replacements),backup)
        logging.info('Repaired %d inventories in %s; original in %s',len(repaired),snapshot.source_path,backup)
        return {'inventories':len(repaired),'backup':str(backup)}

    def release_orphan_guns(self,snapshot):
        """Fix, on open, saves where a workshop vehicle's gun is bound to a place
        the vehicle no longer has (the game crashes spawning it). The original
        is kept in backups/saves first."""
        from .campaign import build_save_copy, release_orphan_guns
        from .syntax import parse, entity_root
        from .transactions import assert_game_closed, replace_file_verified
        if getattr(snapshot,'_scene',None) is None: return None
        folder=self.addon_root()/'resource'/'entity'/'-vehicle'/'workshop'
        places={}
        if folder.is_dir():
            for path in folder.glob('mw_*/mw_*.def'):
                if path.stem!=path.parent.name: continue
                try:root=entity_root(parse(path.read_bytes()))
                except Exception:continue
                places[path.stem]={p.args[0] for w in root.find_all('Weaponry') for p in w.find_all('place') if p.args}
        if not places: return None
        replacements,released=release_orphan_guns(snapshot,places)
        if not replacements: return None
        assert_game_closed()
        self.make_room(snapshot.source_path)
        backup=self.workspace/'backups'/'saves'/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+snapshot.source_path.name)
        replace_file_verified(snapshot.source_path,snapshot.source_hash,build_save_copy(snapshot,replacements),backup)
        logging.info('Released %d guns bound to missing places in %s: %s; original in %s',len(released),snapshot.source_path,released,backup)
        return {'released':released,'backup':str(backup)}

    def reconcile_crews(self,snapshot):
        """A workshop vehicle never keeps more crew than seats. A rebuild may
        leave fewer (Lazarus_Opti: a 0.6.8 Polsten carried a duplicate crew
        member): the surplus goes to the reserve, as when the vehicle is edited,
        keeping its state and loaded weapons. The original is kept in
        backups/saves first."""
        from .campaign import plan_edit, apply_plan, load_campaign, CampaignError
        addon=self.addon_root();trimmed=[];seen=set()
        if self.index is None or not (addon/'mod.info').is_file():return trimmed
        while len(trimmed)<128:
            target=None
            for unit in snapshot.units:
                template=re.sub(r'\([^()]*\)$','',str(unit.get('template') or ''))
                if unit.get('kind')!='vehicle' or not template.startswith('mw_') or unit.get('squad_index') is None or unit['eid'] in seen:continue
                try:metadata=json.loads((addon/f'assembly_{template}.json').read_text('utf-8'))['metadata']
                except (OSError,ValueError,KeyError,TypeError):continue
                slots=metadata.get('crew_slots')
                if isinstance(slots,int) and not isinstance(slots,bool) and len(snapshot.squads[unit['squad_index']]['human_eids'])>slots:
                    target=(unit,template,slots,metadata);break
            if target is None:break
            unit,template,slots,metadata=target;seen.add(unit['eid'])
            profile=copy.deepcopy(metadata.get('state_profile') or {})
            for key in ('gun_swap','commander_mg','smoke_launchers'):
                if isinstance(profile.get(key),dict):profile[key]['initialize']=False  # nothing is reloaded
            try:
                plan=plan_edit(snapshot,unit['eid'],template,slots,addon_mod=ADDON_MOD,state_profile=profile,keep_names=True)
                apply_plan(snapshot,plan)  # proves the change before anything is written
            except CampaignError as error:
                logging.warning('Crew of %s (%s) left as it is: %s',template,unit['eid'],error);continue
            # The save and the addon's new reserve squad are published together;
            # the operation keeps the original save.
            from .registration import register_crew_reserves
            self.make_room(snapshot.source_path)
            operation=self.workspace/'operations'/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_crew_'+secrets.token_hex(3))
            self._storage_publish(operation,snapshot,plan,lambda transaction:None,
                                  stage=lambda staging:register_crew_reserves(self.index,staging,plan.get('reserve_squads') or []))
            logging.info('Crew of %s (%s) fitted to %d seats; surplus to reserve; original in %s',template,unit['eid'],slots,operation)
            self.record_history('crew_fitted',{'vehicle':template,'eid':unit['eid'],'crew':slots,'save':snapshot.source_path.name,'operation':operation.name})
            trimmed.append({'vehicle':template,'crew':slots,'backup':str(operation)})
            snapshot=load_campaign(snapshot.source_path)
        return trimmed

    def owned_addon(self,addon):
        """The shared addon's files, checked against the tool's own record of them.

        Something outside the tool may change them (a vehicle folder deleted by
        hand, an antivirus, a copy restored): the player then could build
        nothing ever again. If the addon is still the tool's own, the current
        files are kept in backups/addon first, what changed is written to the
        log and the build history, and they become the new record. An addon
        that is not the tool's stays untouched.
        """
        from .transactions import OWNERSHIP_FILE, TransactionError, _owned_addon, _tree_hashes
        try:
            return _owned_addon(addon,ADDON_MOD)
        except TransactionError:
            try:marker=json.loads((addon/OWNERSHIP_FILE).read_text(encoding='utf-8'))
            except (OSError,ValueError):raise TransactionError('Existing addon is not owned by this workshop; choose another destination') from None
            if marker.get('addon_mod')!=ADDON_MOD:raise
        self.closed()
        expected=marker.get('files',{})
        actual={name:digest for name,digest in _tree_hashes(addon).items() if name!=OWNERSHIP_FILE}
        change={'added':sorted(set(actual)-set(expected)),'removed':sorted(set(expected)-set(actual)),
                'changed':sorted(name for name in set(actual)&set(expected) if actual[name]!=expected[name])}
        backup=self.workspace/'backups'/'addon'/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_changed_outside')
        self.make_room(addon)
        shutil.copytree(long_path(addon),long_path(backup))
        for old in sorted(backup.parent.glob('*_changed_outside'))[:-3]:  # a whole addon each: keep the latest three
            shutil.rmtree(long_path(old),ignore_errors=True)
        marker['files']=actual
        (addon/OWNERSHIP_FILE).write_text(json.dumps(marker,sort_keys=True),encoding='utf-8')
        logging.warning('Shared addon changed outside the tool; adopted after a backup in %s: %s',backup,change)
        self.record_history('addon_changed_outside',{**change,'backup':backup.name})
        return _owned_addon(addon,ADDON_MOD)

    def _storage_snapshot(self,body):
        self.closed()
        if self.snapshot is None:raise ValueError('Seleccioná primero una campaña.')
        if body.get('source_hash')!=self.snapshot.source_hash:
            raise ValueError('La campaña cambió. Volvé a abrirla antes de trasladar el vehículo.')
        if body.get('source_path') and Path(body['source_path']).resolve()!=self.snapshot.source_path:
            raise ValueError('La campaña cambió. Volvé a abrirla antes de trasladar el vehículo.')
        return self.snapshot

    def _storage_finish(self,source,event,data):
        with self.lock:
            stale,self.index=self.index,None
            self.previews.clear();self.__dict__.pop('checks',None);self.__dict__.pop('crew_models',None)
            self.last_result=None
        self.stop_catalog_warmup()
        if stale:stale.close()
        self.record_history(event,data)
        self.load_save({'path':str(source)})
        return self.campaign_data()

    def _storage_publish(self,operation,snapshot,plan,before_commit,stage=None):
        from .transactions import prepare_transaction,commit_transaction,OWNERSHIP_FILE,_read_journal
        addon=self.addon_root();hashes=self.owned_addon(addon) if addon.exists() else None
        staging=operation/ADDON_ID
        if addon.exists():
            shutil.copytree(long_path(addon),long_path(staging),ignore=lambda directory,names:[OWNERSHIP_FILE] if OWNERSHIP_FILE in names else [])
        else:
            from .registration import describe_addon
            staging.mkdir(parents=True);describe_addon(staging)
        if stage:stage(staging)
        transaction=prepare_transaction(operation,snapshot,plan,addon_directory=staging)
        before_commit(transaction)
        self.closed()
        if (self.owned_addon(addon) if addon.exists() else None)!=hashes:
            raise ValueError('El mod cambió mientras se preparaba el traslado. Volvé a intentarlo.')
        previous_fence=self.settings.get('addon_publication')
        self.mark_addon_published(pending=True)
        try:
            with self.phase('Storage: publish mod and campaign'):
                result=commit_transaction(transaction['journal_path'],addon_destination=addon,
                                          save_destination=snapshot.source_path,allow_install=True,in_place=True,phase=self.phase)
        except Exception:
            if _read_journal(transaction['journal_path'])[1]['state'] in ('prepared','rolled_back'):
                if previous_fence is None:self.settings.pop('addon_publication',None)
                else:self.settings['addon_publication']=previous_fence
                self.save_settings()
            raise
        self.mark_addon_published()
        return result

    def remove_vehicle(self,body):
        """Compatibility endpoint: removing now always preserves the whole unit."""
        return self.store_vehicle(body)

    def store_vehicle(self,body):
        from .vehicle_storage import VehicleStorage,plan_storage,_stored_records
        with self.storage_lock:
            snapshot=self._storage_snapshot(body)
            unit=next((u for u in snapshot.units if u['eid']==body.get('eid')),None)
            template=re.sub(r'\([^()]*\)$','',str((unit or {}).get('template') or ''))
            if not unit or unit['kind']!='vehicle':
                raise ValueError('Elegí un vehículo de la campaña para guardarlo en el Almacén.')
            # Prove the archive is separable and restorable before removing it.
            plan=plan_storage(snapshot,unit['eid'],addon_mod=ADDON_MOD)
            _stored_records(snapshot,unit['eid'])
            storage=VehicleStorage(self.workspace);identifier,folder=storage.allocate()
            shown=next((u for u in self.campaign_data().get('units',[]) if u['eid']==unit['eid']),unit)
            crew=[u for u in snapshot.units if u['eid'] in plan['stored_eids'] and u['kind']=='human']
            try:
                capacity=json.loads((self.addon_root()/f'assembly_{template}.json').read_text('utf-8'))['metadata']['crew_slots']
            except (OSError,ValueError,KeyError,TypeError):capacity=None
            self._storage_publish(folder/'store',snapshot,plan,
                lambda transaction:storage.create(identifier,snapshot.source_path,shown,crew,transaction,crew_slots=capacity,
                    campaign_name=snapshot.campaign_name,army=snapshot.army))
            campaign=self._storage_finish(snapshot.source_path,'stored',{'vehicle':template,'storage_id':identifier,'crew':len(crew)})
            return {'stored':identifier,'crew':len(crew),'backup':str(folder),'campaign':campaign}

    def restore_stored_vehicle(self,body):
        from .vehicle_storage import VehicleStorage,plan_restoration
        from .compiler import validate_linkbone_strings
        with self.storage_lock:
            snapshot=self._storage_snapshot(body);storage=VehicleStorage(self.workspace)
            missing=self.campaign_data().get('unresolved_mods') or []
            if missing:raise ValueError('La campaña requiere mods que no se encontraron: '+', '.join(missing))
            folder,data,original,journal=storage.get(body.get('id'),snapshot.source_path)
            template=re.sub(r'\([^()]*\)$','',data['template']);addon=self.addon_root()
            def mod_key(value):return value.rsplit(':',1)[0].removeprefix('mod_').casefold()
            active={mod_key(value) for value in snapshot.mods}
            required=[value for value in original.mods if not is_own_mod(value) and mod_key(value) not in active]
            if required:
                raise ValueError('La campaña de destino necesita los mods de origen: '+', '.join(required))
            recipe=addon/f'assembly_{template}.json'
            model=addon/'resource/entity/-vehicle/workshop'/template
            plan=plan_restoration(snapshot,original,data['eid'],addon_mod=ADDON_MOD)
            if re.fullmatch(r'mw_[A-Za-z0-9_-]+',template):
                if not recipe.is_file() or not (model/f'{template}.def').is_file():
                    raise ValueError('Faltan archivos del vehículo. Actualizá los vehículos antes de devolverlo. La copia del Almacén se conserva.')
                metadata=json.loads(recipe.read_text('utf-8'))['metadata']
                capacity=data.get('crew_slots')
                if metadata.get('crew_slots')!=(capacity if capacity is not None else plan['crew_slots']):
                    raise ValueError('Los puestos del vehículo cambiaron. No se devolvió para conservar toda su tripulación.')
                for path in model.rglob('*'):
                    if path.suffix.casefold() in ('.def','.ext','.mdl'):
                        validate_linkbone_strings(path.read_bytes(),path.name)
            else:
                if self.index is None:self.index_resources()
                try:self.index.resolve_entity(template)
                except (FileNotFoundError,ValueError,KeyError):
                    raise ValueError('El vehículo no está disponible con los mods de destino. La copia del Almacén se conserva.') from None
            operation=folder/'restores'/secrets.token_hex(16)
            self._storage_publish(operation,snapshot,plan,
                lambda transaction:storage.restoring(data['id'],snapshot.source_path,transaction))
            campaign=self._storage_finish(snapshot.source_path,'restored',{'vehicle':template,'storage_id':data['id']})
            return {'restored':data['id'],'eid':plan['new_eid'],'campaign':campaign}

    def recover_stored_vehicle(self,body):
        from .vehicle_storage import VehicleStorage
        from .transactions import rollback_transaction
        with self.storage_lock:
            snapshot=self._storage_snapshot(body)
            journal=VehicleStorage(self.workspace).pending(body.get('id'),snapshot.source_path)
            rollback_transaction(journal)
            self.mark_addon_published()
            campaign=self._storage_finish(snapshot.source_path,'storage_recovered',{'storage_id':body['id']})
            return {'recovered':body['id'],'campaign':campaign}

    def record_history(self,event,data):
        """One line per vehicle built, installed or updated, never pruned: the
        error report carries the whole story of a player's vehicles."""
        try:
            folder=self.workspace/'history';folder.mkdir(parents=True,exist_ok=True)
            row={'time':datetime.now().isoformat(timespec='seconds'),'event':event,'tool_version':__version__,**serial(data)}
            with (folder/'builds.jsonl').open('a',encoding='utf-8') as out:out.write(json.dumps(row,ensure_ascii=False)+'\n')
        except OSError as error:
            logging.warning('Build history not written: %s',error)

    def campaign_data(self):
        """The opened campaign with readable labels and its workshop depot."""
        data=dict(getattr(self,'campaign_view',None) or {})
        if data:
            data['depot']=Depot(self.workspace).items(data['source_path'])
            from .vehicle_storage import VehicleStorage,StorageError
            try:
                storage=VehicleStorage(self.workspace)
                data['stored_vehicles']=storage.items(data['source_path'])
                if storage.problems:data['storage_problem']=storage.problems[0]
            except (StorageError,OSError,ValueError,KeyError) as error:
                data['stored_vehicles']=[];data['storage_problem']=str(error)
        return data

    def preview(self,body):
        from .geometry import build_preview
        if self.index is None: self.index_resources()
        key=(body['template'],body.get('part','full'),body.get('mount'))
        if key not in self.previews:
            from .compiler import seat_markers
            try: seats=seat_markers(self.index,key[0])
            except Exception as error:  # seats are a view aid; never block the model
                logging.info('seat markers unavailable for %s: %s',key[0],error); seats=[]
            value=build_preview(self.index,key[0],key[1],mount_name=key[2],seats=seats)
            if not value.get('meshes'): raise ValueError('No se pudo cargar geometría visible para esta unidad. '+ '; '.join(d.get('message','') for d in value.get('diagnostics',[])))
            if len(self.previews)>=8: self.previews.pop(next(iter(self.previews)))
            self.previews[key]=value
        return self.previews[key]

    def crew_model(self,pose):
        """Generic soldier meshes in a seat pose for the 3D view (cached)."""
        from .crew_model import posed_soldier
        if self.index is None: raise ValueError('Leé los recursos primero.')
        pose=str(pose or '')[:120]
        if pose and not re.fullmatch(r'[A-Za-z0-9_\-]+',pose): raise ValueError('Pose inválida.')
        cache=self.__dict__.setdefault('crew_models',{})
        key=(id(self.index),pose)
        if key not in cache:
            cache.clear() if len(cache)>64 else None
            cache[key]=posed_soldier(self.index,pose or None)
        return cache[key]

    def texture(self,mtl,portrait_token=None):
        """Diffuse map of a material as PNG for the 3D view, cached on disk by source identity."""
        from .textures import diffuse_path, material_png
        if portrait_token:
            context=self.portrait_previews.get(portrait_token)
            if context is None:context=next((c for c in getattr(self,'design_previews',{}).values() if c['token']==portrait_token),None)
            draft=getattr(self,'gun_draft_preview',None)
            if context is None and draft and draft['token']==portrait_token:context=draft
            if context is None: raise ValueError('La vista del retrato venció. Cargala de nuevo.')
            index=context['index']
        else:
            if self.index is None: self.index_resources()
            index=self.index
        if not str(mtl).lower().endswith('.mtl') or not index.exists(mtl): raise ValueError('Material inexistente')
        source=diffuse_path(index,mtl)
        if source is None: return None  # glass, bare parts: drawn untextured, not an error
        entry=index.resolve(source).as_dict()
        key=hashlib.sha256(json.dumps([source,entry.get('size'),entry.get('crc'),entry.get('modified_ns'),entry.get('source_root')]).encode()).hexdigest()[:24]
        cache=self.workspace/'texture_cache'/(key+'.png')
        if cache.is_file(): return cache.read_bytes()
        data=material_png(index,mtl)
        cache.parent.mkdir(parents=True,exist_ok=True); cache.write_bytes(data)
        return data

    def screenshot(self,body):
        """Save a capture of the 3D view to Pictures/Cursed Workshop and show it in Explorer."""
        import base64
        data=str(body.get('image',''))
        if not data.startswith('data:image/png;base64,'): raise ValueError('Captura inválida.')
        raw=base64.b64decode(data.split(',',1)[1],validate=True)
        if not raw.startswith(bytes([0x89])+b'PNG'): raise ValueError('Captura inválida.')
        folder=Path.home()/'Pictures'/'Cursed Workshop'
        folder.mkdir(parents=True,exist_ok=True)
        name=re.sub(r'[^\w\- ]+','',str(body.get('name') or 'vehiculo'))[:60].strip() or 'vehiculo'
        target=folder/f"{name} {datetime.now().strftime('%Y-%m-%d %H-%M-%S')}.png"
        target.write_bytes(raw)
        if os.name=='nt' and not body.get('quiet'):
            subprocess.Popen(['explorer','/select,',str(target)])
        return {'path':str(target)}

    def error_report(self,body):
        """The zip a player posts in #bug-reports, shown selected in Explorer.
        Only the newest ten reports are kept."""
        save=self.report_save()
        if save is None:
            raise ValueError('Seleccioná primero una campaña.')
        from .error_report import build_report
        folder=self.workspace/'reports'
        game=config_dirs(self.settings.get('game_root'))[0]  # where the game really writes (AppData, OneDrive, Linux)
        addon=self.addon_root() if self.settings.get('game_root') else None
        result=build_report(folder,game,self.workspace,str(body.get('diagnostic') or '')[:400_000],addon=addon,save=save)
        for old in sorted(folder.glob('Cursed Workshop report *.zip'),reverse=True)[10:]:
            old.unlink(missing_ok=True)
        if os.name=='nt' and not body.get('quiet'):
            subprocess.Popen(['explorer','/select,',result['path']])
        return result

    def report_save(self):
        """Only the campaign explicitly open in this session can be reported."""
        if self.snapshot is not None and Path(self.snapshot.source_path).is_file():
            return Path(self.snapshot.source_path)
        return None

    def free_anchor(self,body):
        """Free-mount position for a chassis + module pair (same rule as the compiler)."""
        from .geometry import free_anchor, free_mount_spec, parse_model, whole_model_mount
        if self.index is None: self.index_resources()
        hull=self.index.resolve_template(body['hull']); donor=self.index.resolve_template(body['donor'])
        hull_model=parse_model(self.index.read(hull['model_path'])); donor_model=parse_model(self.index.read(donor['model_path']))
        mount=body.get('donor_mount') or donor_model['anchor_name'] or whole_model_mount(donor_model)
        spot=free_mount_spec(body.get('recipient_mount'))[1]
        return {'anchor':free_anchor(self.index,hull_model,hull['model_path'],donor_model,donor['model_path'],mount,spot)}

    def catalog_folder(self):
        return self.workspace/'cache'/'catalogs'

    def warm_catalogs(self):
        """Load or read the whole-game catalogs in the background as soon as the
        sources are indexed, so armor, supply and commander lists open at once."""
        from .cancellation import current_cancel
        from .catalog_cache import CATALOGS, load, awaited
        self.stop_catalog_warmup()
        with self.lock:
            index=self.index;cancel=self.catalog_warmup=threading.Event()
        state=self
        class Yielding:
            # Python runs one thread at a time: while the player's own operation
            # runs, the warmup waits, unless that operation waits for a catalog.
            def is_set(self):
                while state.foreground and not awaited() and not cancel.is_set():time.sleep(.05)
                return cancel.is_set()
        def work():
            current_cancel.set(Yielding())
            for name in CATALOGS:
                if cancel.is_set() or self.index is not index:return
                started=time.perf_counter()
                try:load(index,name,self.catalog_folder())
                except Exception as error:
                    if not cancel.is_set():logging.warning('Catalog %s not prepared: %s',name,error)
                    return
                logging.info('Phase timing: Prepare catalog %s | done | elapsed_ms=%.0f',name,(time.perf_counter()-started)*1000)
        threading.Thread(target=work,name='workshop-catalogs',daemon=True).start()

    def passenger_listing(self,root,model,frame):
        from .compiler import _crew_expansion, _expand_place_macros
        from .passenger_seats import listing
        root=root.clone()
        _crew_expansion(self.index,root);_expand_place_macros(self.index,root,lambda nodes,places:True)
        return listing(root,model,frame)

    def stop_catalog_warmup(self):
        event=self.__dict__.get('catalog_warmup')
        if event:event.set()

    def commander_catalog(self):
        from .commander_mg import weapon_catalog, support_catalog
        from .catalog_cache import load
        if self.index is None: raise ValueError('Leé los recursos primero.')
        load(self.index,'commander_weapons',self.catalog_folder());load(self.index,'commander_supports',self.catalog_folder())
        return {'weapons':weapon_catalog(self.index), 'supports':support_catalog(self.index),
                'operator':'commander', 'dlc':self.index.dlc_availability().public(), 'runtime_verified':False}

    def rider_catalog(self,body=None):
        from .rider_mg import catalog, weapon_catalog
        from .catalog_cache import load
        if self.index is None: self.index_resources()
        load(self.index,'commander_weapons',self.catalog_folder());load(self.index,'commander_supports',self.catalog_folder())
        value=load(self.index,'rider_mgs',self.catalog_folder())
        return {'supports':[{k:v for k,v in s.items() if k in ('id','label','pose','turns','yaw_limits','pitch_limits')} for s in value['supports']],
                'poses':value['poses'],'weapons':[{'id':w['id'],'label':w['label']} for w in weapon_catalog(self.index)],
                'dlc':self.index.dlc_availability().public(), 'runtime_verified':False}

    def passenger_catalog(self,body=None):
        from .catalog_cache import load
        if self.index is None: self.index_resources()
        return {'poses':load(self.index,'passenger_poses',self.catalog_folder()),'runtime_verified':False}

    def rider_preview(self,body):
        from .rider_mg import preview
        if self.index is None: self.index_resources()
        return preview(self.index,body)

    def commander_preview(self,body):
        from .commander_mg import configuration, _stock
        from .geometry import build_preview, parse_model, inverse, multiply, point, attached_actor_frame
        if self.index is None: raise ValueError('Leé los recursos primero.')
        support,weapon,spec=configuration(self.index,body)
        base=_stock(self.index)
        model=parse_model(base.read(support['model_path']))
        pivot=model['bones'][support['yaw']]['world']
        meshes=build_preview(base,support['template'],'turret',support['yaw'])['meshes']
        # The weapon-angle view turns these: the cradle under the pitch bone
        # and the weapon with the pitch, everything else only with the yaw.
        from .geometry import descendants
        pitched=descendants(model['bones'],support['pitch'])
        for mesh in meshes:mesh['rig_joint']='pitch' if mesh.get('bone') in pitched else 'yaw'
        # build_preview removes translation only; make everything pivot-local,
        # including the native pivot's orientation/reflection.
        linear=inverse([row[:3]+[0] for row in pivot[:3]]+[[0,0,0,1]])
        for mesh in meshes:
            mesh['positions']=[v for i in range(0,len(mesh['positions']),3)
                               for v in point(linear,mesh['positions'][i:i+3])]
            mesh['normals']=[]
        attachment=multiply(inverse(pivot),model['bones'][support['link']]['world'])
        weapon_source=self.index.mountable_game()
        gun=build_preview(weapon_source,weapon['entity'],'full')
        weapon_model=parse_model(weapon_source.read(gun['model_path']))
        # An attached actor's root is placed on LinkBone. The standalone root
        # transform (often reflected) must not be applied a second time.
        attachment=attached_actor_frame(weapon_model,attachment)
        for mesh in gun['meshes']:
            mesh['positions']=[v for i in range(0,len(mesh['positions']),3)
                               for v in point(attachment,mesh['positions'][i:i+3])]
            mesh['normals']=[]
            mesh['rig_joint']='pitch'
            meshes.append(mesh)
        seat=multiply(inverse(pivot),model['bones'][support['seat']]['world'])
        return {'meshes':meshes,'pitch_matrix':multiply(inverse(pivot),model['bones'][support['pitch']]['world']),
                'seats':[{'place':'commander','group':'crew','bone':'cmdmg_seat',
                 'position':point(seat,[0,0,0]),'rotation':[row[:3] for row in seat[:3]],'pose':support['pose']}],
                'diagnostics':[],'mounts':[], 'internal_crew':0}

    def check(self,body):
        """Dry run of a chassis + module pair before the user presses Build.

        Compiles and registers into a throwaway folder (nothing reaches the
        addon or the save), so the same failures a build would hit show up as
        soon as both parts are chosen. Only the mounts matter here: moving the
        module never decides whether a pair can be built.
        """
        from .compiler import compile_assembly
        from .registration import register_variant
        if self.index is None: self.index_resources()
        transform=dict(body.get('transform') or {})
        transform.update({key:body[key] for key in ('recipient_mount','donor_mount','crew_exclude') if body.get(key)})
        key=json.dumps([body['hull'],body['donor'],transform,self.index.dlc_availability().key()],sort_keys=True)
        cache=self.__dict__.setdefault('checks',{})
        if key not in cache:
            with tempfile.TemporaryDirectory(prefix='mw_check_') as temp:
                try:
                    result=copy.deepcopy(self.design_context({'hull':body['hull'],'transform':transform})['result']) if transform.get('assembly_draft') else compile_assembly(self.index,body['hull'],body['donor'],transform,Path(temp)/'compile','mw_check')
                    result['campaign_plan']={}
                    register_variant(self.index,result,Path(temp)/ADDON_ID,'mw_check','check')
                    from .geometry import parse_model, free_mount_options, free_mount_parent, free_spot_parent, point, inverse, multiply, identity
                    model=parse_model((Path(result['output_dir'])/'resource'/result['model_path']).read_bytes())
                    parents={free_mount_parent(model)}
                    parents.update(free_spot_parent(model,spot[6:]) for spot in free_mount_options(model) if spot.startswith('@free:'))
                    from .pivots import pose_matrix
                    display_frame=inverse(pose_matrix(transform.get('hull') or {})) if transform.get('assembly_draft') else identity()
                    hosts=[{'name':name,'matrix':multiply(display_frame,model['bones'][name]['world'])} for name in sorted(parents) if name in model['bones']]
                    locator=next((model['bones'][name] for name in ('commander_rot','commander') if name in model['bones']),None)
                    from .extra_armor import parents as armor_parents
                    from .syntax import parse, entity_root
                    from .compiler import _explicit_weapon_operators
                    from .commander_mg import operator_weapon_problem, operator_loader_warning
                    final_root=entity_root(parse((Path(result['output_dir'])/'resource'/result['definition_path']).read_bytes()))
                    _explicit_weapon_operators(self.index,final_root)
                    commander_problem=''
                    if not transform.get('commander_mg'):
                        commander_problem=operator_weapon_problem(final_root)
                    value={'ok':True,'crew':result.get('metadata',{}).get('crew_roles') or [], 'hosts':hosts,
                           'commander_problem':commander_problem,
                           'commander_bones':(result.get('metadata',{}).get('commander_mg') or {}).get('bones',{}),
                           'commander_warning':operator_loader_warning(final_root),
                           'armor_hosts':[{**host,'matrix':multiply(display_frame,host['matrix'])} for host in armor_parents(model)],
                           'crew_visibility':result.get('metadata',{}).get('crew_visibility',[]),
                           'passengers':self.passenger_listing(final_root,model,display_frame),
                           'commander_position':point(display_frame,point(locator['world'],[0,0,2]) if locator else model['anchor'])}
                except Exception as error:
                    from .cancellation import JobCancelled
                    if isinstance(error, JobCancelled):raise
                    self.record_error(error,'Compatibility check')
                    value={'ok':False,'message':friendly_error(error)}
            if len(cache)>=32: cache.pop(next(iter(cache)))
            cache[key]=value
        return cache[key]

    def gun_catalog(self,body):
        from .gun_assembly import discover
        if self.index is None:self.index_resources()
        return discover(self.index,body['template'])

    def support_catalog(self, body=None):
        from .support_vehicle import public_catalog
        from .catalog_cache import load
        if self.index is None: self.index_resources()
        load(self.index,'support',self.catalog_folder())
        return public_catalog(self.index)

    def smoke_preview(self, body):
        from .smoke_launchers import preview
        return preview(body.get('tubes'))

    def armor_catalog(self, body=None):
        from .extra_armor import public_catalog
        from .catalog_cache import load
        if self.index is None: self.index_resources()
        load(self.index,'armor',self.catalog_folder())
        return public_catalog(self.index)

    def armor_preview(self, body):
        from .extra_armor import preview
        if self.index is None: self.index_resources()
        return preview(self.index, body['asset'])

    def support_preview(self, body):
        from .support_vehicle import preview
        if self.index is None: self.index_resources()
        return preview(self.index, body['template'], body['support_vehicle'])

    def gun_preview(self,body):
        from .gun_assembly import preview_parts,compile_guns
        if self.index is None:self.index_resources()
        if body.get('gun_swaps'):
            from .geometry import build_preview
            from .compiler import seat_markers
            temp=tempfile.TemporaryDirectory(prefix='cw_gun_preview_');view=None
            try:
                folder=Path(temp.name)/'compiled';identifier='mw_gun_preview'
                compile_guns(self.index,body['hull'],body['donor'],{'gun_swaps':body['gun_swaps']},folder,identifier)
                view=self.index.with_overlay(folder)
                if body.get('gun_swap'):
                    from .gun_assembly import selected
                    _,_,_,model,_,spec=selected(self.index,body['hull'],body['gun_swap']['recipient_place'])
                    result=preview_parts(view,identifier,body['donor'],body['gun_swap'],
                        recipient_frame=model['bones'][spec['pivot']]['world'])
                else:
                    hull=build_preview(view,identifier,'full',seats=seat_markers(view,identifier),include_inventory=True)
                    result={'hull':hull,'turret':{'meshes':[],'seats':[],'joints':[],'mounts':[],
                        'anchor_name':'','anchor':[0,0,0],'seat':0,'diagnostics':[],'stats':{'meshes':0,'triangles':0}},
                        'preview_valid':True,'runtime_verified':False}
                token=secrets.token_urlsafe(24)
                for part in ('hull','turret'):
                    for mesh in result[part]['meshes']:
                        for sub in mesh.get('submeshes',[]):sub['texture_token']=token
                self.clear_gun_preview()
                self.gun_draft_preview={'index':view,'temp':temp,'token':token,'created':time.time()}
                return result
            except BaseException:
                if view:view.close()
                temp.cleanup();raise
        if not body.get('gun_swap'):
            from .geometry import build_preview
            from .compiler import seat_markers
            return {'hull':build_preview(self.index,body['hull'],'full',seats=seat_markers(self.index,body['hull']),include_inventory=True),
                'turret':{'meshes':[],'seats':[],'joints':[],'mounts':[],'anchor':[0,0,0],'anchor_name':'','seat':0,'diagnostics':[]},
                'preview_valid':True,'runtime_verified':False}
        return preview_parts(self.index,body['hull'],body['donor'],body['gun_swap'])

    def design_context(self,body):
        from .draft import compile_draft
        if self.index is None:self.index_resources()
        transform=body['transform'];signature=copy.deepcopy(transform)
        hull=signature.get('hull') or {}
        from .pivots import pose_matrix
        pose_matrix(hull)  # An identity cache hit must not hide an invalid pivot.
        if not any(hull.get('position',[])) and not any(hull.get('rotation',[])):signature.pop('hull',None)
        key=json.dumps([body['hull'],signature,self.index.dlc_availability().key()],sort_keys=True)
        contexts=self.__dict__.setdefault('design_previews',{})
        if key in contexts:
            cached=contexts.pop(key);contexts[key]=cached;return cached
        temp=tempfile.TemporaryDirectory(prefix='cw_design_preview_');view=None
        try:
            from .draft import design_turrets
            draft=transform['assembly_draft'];donor=([*design_turrets(draft),*(draft.get('guns') or []),{}][0]).get('donor_template') or body['hull']
            with self.phase('Design: compile vehicle'):
                result=compile_draft(self.index,body['hull'],donor,transform,Path(temp.name)/'compiled','mw_design_preview')
            with self.phase('Design: index compiled output'):
                view=self.index.with_overlay(Path(temp.name)/'compiled')
            context={'key':key,'index':view,'temp':temp,'token':secrets.token_urlsafe(24),'result':result}
            if len(contexts)>=3:
                old=contexts.pop(next(iter(contexts)));old['index'].close();old['temp'].cleanup()
            contexts[key]=context;return context
        except BaseException:
            if view:view.close()
            temp.cleanup();raise

    def design_catalog(self,body):
        from .gun_assembly import discover
        body=self.design_display_body(body)
        context=self.design_context(body)
        with self.phase('Design: discover weapons'):
            return discover(context['index'],'mw_design_preview')

    @staticmethod
    def design_display_body(body):
        """Catalog and display share the same unposed source context."""
        body=copy.deepcopy(body)
        # The crew editor applies these live over the unadjusted seats. Baking
        # them here would rotate twice and remove unchecked workers from the UI.
        body['transform'].pop('crew_seats',None)
        body['transform'].pop('crew_exclude',None)
        hull_pose=body['transform'].get('hull') or {}
        from .pivots import pose_matrix
        pose_matrix(hull_pose)
        if any(hull_pose.get('position',[])) or any(hull_pose.get('rotation',[])):
            body['transform']['hull']={'position':[0,0,0],'rotation':[0,0,0]}
        return body

    def design_preview_data(self,body):
        from .geometry import build_preview
        from .compiler import seat_markers
        from .gun_assembly import preview_parts
        excluded=set((body.get('transform') or {}).get('crew_exclude') or [])
        body=self.design_display_body(body)
        pending=body.get('pending')
        if pending and pending['kind'] in ('turret','native'):
            # The other saved turrets stay; the mount being changed shows bare.
            from .draft import design_turrets
            body=copy.deepcopy(body);draft=body['transform']['assembly_draft']
            target=pending.get('recipient_mount') or 'turret'
            draft['turrets']=[row for row in design_turrets(draft) if (row.get('recipient_mount') or 'turret')!=target]
            draft['removals']=[row for row in draft.get('removals',[]) if row['mount']!=target]
            draft.pop('turret',None)
            if pending['kind']=='turret':draft['guns']=[]
        context=self.design_context(body);index=context['index'];template='mw_design_preview'
        if pending and pending['kind']=='gun':
            result=preview_parts(index,template,pending['donor_template'],pending,
                recipient_frame=context['result']['metadata']['draft_frames'].get(pending['recipient_place']))
        elif pending and pending['kind']=='turret':
            if pending.get('assembly_scope') == 'complete':
                from .modular_assembly import resolve_complete
                pending={**pending,'donor_mount':resolve_complete(index,pending['donor_template'])['root']}
            result={'hull':build_preview(index,template,'hull',pending.get('recipient_mount')),
                'turret':build_preview(index,pending['donor_template'],'turret',pending.get('donor_mount'),seats=seat_markers(index,pending['donor_template']))}
            # A removed/replaced turret must not raise the preview's deck.
            # The original campaign template is no longer the mount authority.
            from .geometry import free_mount_spec, free_anchor, parse_model
            free,spot=free_mount_spec(result['hull']['anchor_name'])
            if free:
                hull_info=index.resolve_template(template);donor_info=index.resolve_template(pending['donor_template'])
                result['hull']['free_anchor']=free_anchor(index,
                    parse_model(index.read(hull_info['model_path'])),hull_info['model_path'],
                    parse_model(index.read(donor_info['model_path'])),donor_info['model_path'],result['turret']['anchor_name'],spot)
        elif pending and pending['kind']=='native':
            mount=pending['recipient_mount']
            result={'hull':build_preview(index,template,'hull',mount,seats=seat_markers(index,template)),
                'turret':build_preview(index,template,'turret',mount,seats=seat_markers(index,template))}
            # It already occupies its authored socket. Re-applying automatic
            # seating would move the native turret just by selecting it.
            result['hull']['seat']=result['turret']['seat']=0
            result['turret']['native_module']=True
        else:
            result={'hull':build_preview(index,template,'full',seats=seat_markers(index,template),include_inventory=True),
                'turret':{'meshes':[],'seats':[],'joints':[],'mounts':[],'anchor':[0,0,0],'anchor_name':'','seat':0,'diagnostics':[]}}
        # Editable fittings are rendered by their dedicated frontend groups.
        # Keep inherited fittings, but avoid drawing newly compiled ones twice.
        metadata=context['result']['metadata'];editable_bones=set()
        for fitting in metadata.get('extra_armor') or []:editable_bones.update(fitting.get('bones',[]))
        for fitting in (metadata.get('smoke_launchers') or {}).get('mounts',[]):editable_bones.add(fitting['root'])
        editable_bones.update((metadata.get('commander_mg') or {}).get('bones',{}).values())
        for mount in (metadata.get('rider_mgs') or {}).get('mounts',[]):editable_bones.update(mount.get('bones',{}).values())
        for part in ('hull','turret'):
            result[part]['meshes']=[m for m in result[part]['meshes'] if m.get('bone') not in editable_bones]
            for mesh in result[part]['meshes']:
                for sub in mesh.get('submeshes',[]):sub['texture_token']=context['token']
        result['saved_weapons']=[{'recipient_place':c['recipient']['place'],'donor_place':c['donor']['place'],
            'donor_template':c['donor'].get('template'),'label':index.weapon_label(c['donor']['weapon'])}
            for c in context['result']['metadata'].get('gun_assemblies',[])]
        result.update(preview_valid=True,runtime_verified=False,
                      crew_visibility=[r for r in metadata.get('crew_visibility',[]) if r['place'] not in excluded]);return result

    def clear_gun_preview(self):
        draft=self.__dict__.pop('gun_draft_preview',None)
        if draft:draft['index'].close();draft['temp'].cleanup()

    def edit_source(self,body):
        """Chassis, module and placement of an owned vehicle, for "Editar".

        A workshop vehicle reopens its stored recipe (original parts and
        transform); a game or mod vehicle is its own chassis and module, with
        its turret as the movable part. Building then goes through the same
        compiler as a combination, and only this unit is replaced.
        """
        from .geometry import parse_model
        if self.snapshot is None or self.index is None: raise ValueError('Abrí una campaña y sus recursos primero.')
        units={u['eid']:u for u in (self.campaign_data().get('units') or self.snapshot.to_dict()['units'])}
        unit=units.get(body.get('eid'))
        if not unit or unit['kind']!='vehicle': raise ValueError('Elegí un vehículo de tu partida para editarlo.')
        template=re.sub(r'\([^()]*\)$','',unit['template'])
        recipe=self.addon_root()/f'assembly_{template}.json'
        if re.fullmatch(r'mw_[A-Za-z0-9_-]+',template) and recipe.is_file():
            metadata=json.loads(recipe.read_text(encoding='utf-8')).get('metadata') or {}
            transform=dict(metadata.get('transform') or {})
            recipient,donor=metadata.get('recipient'),metadata.get('donor')
            recipient=recipient.get('id') if isinstance(recipient,dict) else recipient
            donor=donor.get('id') if isinstance(donor,dict) else donor
            transform['recipient_mount']=transform.get('recipient_mount') or metadata.get('recipient_mount')
            transform['donor_mount']=transform.get('donor_mount') or metadata.get('donor_mount')
            from .editable_recipe import reopen
            from .registration import _recipes
            recipes=_recipes(self.addon_root())
            reopened,commander,smoke=transform,metadata.get('commander_mg'),metadata.get('smoke_launchers')
            if recipient in recipes:
                info=self.index.resolve_entity(template)
                model=parse_model(self.index.read(info['model_path']))
                reopened,commander,smoke=reopen({**metadata,'transform':transform},recipes,model)
            return {'eid':unit['eid'],'recipient':recipient,'donor':donor,'recipe':True,
                    'commander_mg':commander, 'smoke_launchers':smoke, 'transform':reopened}
        info=self.index.resolve_entity(template)
        anchor=parse_model(self.index.read(info['model_path']))['anchor_name']
        if not anchor:
            return {'eid':unit['eid'],'recipient':template,'donor':template,
                    'transform':{'commander_only':True},'recipe':False,'full_vehicle':True}
        return {'eid':unit['eid'],'recipient':template,'donor':template,
                'transform':{'recipient_mount':anchor,'donor_mount':anchor},'recipe':False}

    def generate(self,body):
        from .compiler import compile_assembly
        from .registration import (register_variant, rebuild_registrations, recorded_stack, stack_signature, write_stack,
                                   set_campaign, describe_addon)
        from .resources import ResourceIndex
        from .campaign import plan_assembly, plan_refit, plan_edit
        from .transactions import prepare_transaction, OWNERSHIP_FILE, _owned_addon
        self.closed()
        if self.snapshot is None or self.index is None: raise ValueError('Abrí una campaña y sus recursos primero.')
        if getattr(self,'unresolved_mods',[]): raise ValueError('Faltan mods de la campaña: '+', '.join(self.unresolved_mods))
        snapshot=self.snapshot
        data=snapshot.to_dict()
        self.make_room(Path(data['source_path']))
        problem=self.mod_stack_problem(include_generated=False)
        if problem: raise ValueError(problem)
        units={u['eid']:u for u in (self.campaign_data().get('units') or data['units'])}
        depot=Depot(self.workspace)
        def part(kind):
            """A chassis/module comes from an owned vehicle or from the depot."""
            if body.get(kind+'_depot'):
                item=depot.get(data['source_path'],body[kind+'_depot'])
                if item['part']!=('hull' if kind=='recipient' else 'turret'):
                    raise ValueError('Esa pieza del depósito no sirve para esa parte.')
                return {'template':item['template'],'label':item['label'],'depot':item['id'],'eid':None,'mount':item.get('mount')}
            unit=units.get(body.get(kind+'_eid'))
            if not unit or unit['kind']!='vehicle':
                raise ValueError('Elegí un vehículo de tu partida o una pieza del depósito para el chasis y para el módulo.')
            return unit
        supporting=body.get('mode')=='support'
        editing=body.get('mode')=='edit'
        transform=body.get('transform',{})
        drafting=transform.get('assembly_draft') is not None and not editing
        if drafting:
            from .draft import specification
            spec=specification(transform);recipient=part('recipient');resolved=[]
            if not recipient.get('eid'):raise ValueError('Elegí un vehículo receptor de tu campaña.')
            for row in [*spec.get('turrets',[]),*spec.get('guns',[])]:
                if row.get('native'):continue
                if row.get('donor_catalog'):
                    # A catalog part (creative mode) costs no unit: it only has to exist.
                    if not self.index.has_entity(row['donor_template']):
                        raise ValueError('Esa pieza del catálogo no existe con los mods actuales: '+row['donor_template'])
                    continue
                if row.get('donor_depot'):
                    if row.get('assembly_scope')=='complete':
                        raise ValueError('El conjunto completo requiere un donante entero de la campaña.')
                    item=depot.get(data['source_path'],row['donor_depot'])
                    if item['part']!='turret' or item['template']!=row['donor_template']:raise ValueError('El donante guardado ya no coincide con una unidad de la campaña.')
                    if item.get('mount') and row.get('donor_mount')!=item['mount']:
                        raise ValueError('Elegí el montaje guardado en esa pieza del depósito.')
                    continue
                unit=units.get(row.get('donor_eid'))
                if not unit or unit['kind']!='vehicle' or unit['template']!=row['donor_template'] or unit['eid']==recipient['eid']:
                    raise ValueError('El donante guardado ya no coincide con una unidad de la campaña.')
                resolved.append(unit)
            donor=resolved[0] if resolved else {**recipient,'eid':None};source=self.edit_source({'eid':recipient['eid']})
            seed=spec.get('seed')
            if seed:
                if (seed.get('recipient'),seed.get('donor'))!=(source['recipient'],source['donor']):
                    raise ValueError('La receta inicial del diseño no coincide con el vehículo.')
                from .draft import weapon_identity
                if weapon_identity(seed['transform'])!=weapon_identity(source.get('transform') or {}):
                    raise ValueError('La receta inicial del diseño no coincide con el vehículo.')
            editing=False
        elif editing:
            # Editar: one owned vehicle is both parts; nothing goes to the depot.
            source=self.edit_source({'eid':body.get('eid')})
            unit=units[source['eid']]
            recipient={**unit,'template':source['recipient']}
            donor={**unit,'template':source['donor'],'eid':None}
        elif supporting:
            recipient=part('recipient')
            if not recipient.get('eid'):
                raise ValueError('Elegí un vehículo de tu campaña para convertirlo en apoyo.')
            prior = self.addon_root()/('assembly_'+recipient['template']+'.json')
            if prior.is_file() and (json.loads(prior.read_text(encoding='utf-8')).get('metadata',{}).get('support_vehicle')):
                raise ValueError('Ese vehículo ya es de apoyo. Usá Editar para ajustar su carga.')
            donor={**recipient,'eid':None}
        else:
            recipient,donor=part('recipient'),part('donor')
        if recipient.get('eid') and recipient.get('eid')==donor.get('eid'):
            raise ValueError('Elegiste el mismo vehículo como chasis y como módulo. Cambiá una de las dos partes.')
        if not recipient.get('eid') and not donor.get('eid'):
            raise ValueError('Usá al menos un vehículo de tu partida: dos piezas del depósito no tienen tripulación ni lugar en la campaña.')
        transform=body.get('transform',{})
        if not drafting and donor.get('depot') and donor.get('mount'):
            if transform.get('donor_mount') not in (None,donor['mount']):
                raise ValueError('Elegí el montaje guardado en esa pieza del depósito.')
            transform=copy.deepcopy(transform);transform['donor_mount']=donor['mount']
        if transform.get('remove_module') is not None and not editing:
            raise ValueError('Retirá la torreta desde el diseño del vehículo.')
        if editing and bool(transform.get('support_vehicle')) != bool(source.get('transform',{}).get('support_vehicle')):
            raise ValueError('Editar conserva la conversión de apoyo. Para convertir, usá la operación de apoyo.')
        if supporting and not transform.get('support_vehicle'):
            raise ValueError('Elegí un perfil de suministro nativo disponible.')
        if transform.get('support_vehicle') and not (supporting or editing):
            raise ValueError('La conversión de apoyo usa un solo vehículo y retira su torreta.')
        def weapon_choices(tr):
            from .draft import weapon_identity
            return weapon_identity(tr)
        if editing and weapon_choices(transform)!=weapon_choices(source.get('transform',{})):
            raise ValueError('Editar conserva el cañón instalado; para cambiarlo usá dos unidades con Cambiar cañón.')
        if (transform.get('gun_swap') or transform.get('gun_swaps')) and not editing and (not recipient.get('eid') or not donor.get('eid')):
            raise ValueError('Cambiar cañón requiere dos unidades de la campaña; las piezas del depósito no identifican un cañón individual.')
        fingerprint=hashlib.sha256(json.dumps({'hull':recipient['template'],'module':donor['template'],'transform':transform,
            'sources':self.index.sources},sort_keys=True).encode()).hexdigest()[:12]
        new_id='mw_'+fingerprint
        existing=self.addon_root()
        def taken(identifier):
            return self.index.has_entity(identifier) or (existing/'resource'/'entity'/'-vehicle'/'workshop'/identifier).exists()
        attempt=1
        while taken(new_id):  # same pair built again
            new_id='mw_'+hashlib.sha256(f'{fingerprint}:{attempt}'.encode()).hexdigest()[:12]; attempt+=1
        addon_id=ADDON_ID
        operation=self.workspace/'operations'/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+secrets.token_hex(3))
        staging=operation/addon_id
        rebuilt=None
        if existing.is_dir():
            with self.phase('Build: validate and copy existing addon'):
                self.owned_addon(existing)
                shutil.copytree(long_path(existing),long_path(staging),ignore=lambda directory,names:[OWNERSHIP_FILE] if OWNERSHIP_FILE in names else [])
            recorded=recorded_stack(staging)
            if recorded is None or recorded!=stack_signature(self.index) or self.mod_stack_problem():
                # The shared addon was registered for other mods (e.g. built
                # with MACE, now used without): re-register every vehicle.
                with self.phase('Build: register every vehicle again (mods changed)'):
                    base=ResourceIndex(self.settings['game_root'],[root for root in self.settings['mod_roots']
                                                                    if not is_own_mod(Path(root).name)]).build()
                    try: rebuilt=rebuild_registrations(base,staging)
                    finally: base.close()
        else: staging.mkdir(parents=True)
        # The compiler only writes into an empty directory; earlier variants of
        # this campaign live in staging, so compile apart and merge new files.
        compiled=operation/'compile'
        with self.phase('Build: compile vehicle'):
            result=compile_assembly(self.index,recipient['template'],donor['template'],transform,compiled,new_id)
        for path in sorted(long_path(compiled).rglob('*')):
            if path.is_file():
                target=long_path(staging)/path.relative_to(long_path(compiled))
                if target.exists(): raise ValueError(f'El ensamblaje generado choca con un archivo existente del addon: {target.relative_to(long_path(staging))}')
                target.parent.mkdir(parents=True,exist_ok=True); shutil.move(str(path),target)
        shutil.rmtree(long_path(compiled))
        if body.get('portrait'): self.write_portrait(staging,new_id,body['portrait'])
        metadata=result.get('metadata',{})
        if editing and metadata.get('gun_assembly'):
            # Placement edits keep the currently loaded weapon/ammunition.
            metadata['state_profile']['gun_swap']['initialize']=False
        if (editing or drafting) and source.get('commander_mg') and metadata.get('commander_mg'):
            previous=source['commander_mg']
            mount=metadata['state_profile']['commander_mg']
            mount['previous_slot']=previous['slot']
            # Moving a used mount must not spawn another gun or replenish it,
            # even if the player emptied the slot or swapped weapons in battle.
            mount['initialize']=previous.get('item')!=mount['item']
        if (editing or drafting) and source.get('commander_mg') and not metadata.get('commander_mg'):
            metadata.setdefault('state_profile',{})['retired_inventory_users']=[source['commander_mg']['slot']]
        if (editing or drafting) and metadata.get('smoke_launchers'):
            metadata['state_profile']['smoke_launchers']['initialize'] = not bool(source.get('smoke_launchers'))
        if editing:
            from .compiler import _load_entity
            from .syntax import parse
            from .weapon_state import preserve_unchanged_loadout
            _, _, original_root = _load_entity(self.index, units[source['eid']]['template'])
            emitted_root = parse((staging/'resource'/result['definition_path']).read_bytes()).find('game_entity')
            preserve_unchanged_loadout(metadata['state_profile'], original_root, emitted_root)
        crew_slots=metadata.get('crew_slots',len(metadata.get('crew_roles',[])) or None)
        if drafting:
            from .campaign import plan_draft
            plan=plan_draft(snapshot,recipient['eid'],new_id,crew_slots,metadata['draft_steps'],
                addon_mod=ADDON_MOD,state_profile=metadata.get('state_profile'))
        elif editing or supporting:
            plan=plan_edit(snapshot,recipient['eid'],new_id,crew_slots,addon_mod=ADDON_MOD,
                           state_profile=metadata.get('state_profile'),keep_names=True if supporting else source['recipe'])
        elif recipient.get('eid') and donor.get('eid'):
            plan=plan_assembly(snapshot,recipient['eid'],donor['eid'],new_id,crew_slots=crew_slots,
                               addon_mod=ADDON_MOD,state_profile=metadata.get('state_profile'))
        else:
            # One owned vehicle + one depot part: that vehicle is refitted.
            owned,role=(recipient,'hull') if recipient.get('eid') else (donor,'turret')
            plan=plan_refit(snapshot,owned['eid'],new_id,crew_slots,addon_mod=ADDON_MOD,role=role)
        result['campaign_plan']=plan
        def plain(item):  # depot labels read "Torreta de X"; the vehicle name is X
            return re.sub(r'^(Torreta|Chasis) de ','',item.get('label') or item['template'])
        name=body.get('name') or (plain(recipient) if editing or supporting else plain(recipient)+' + '+plain(donor))
        with self.phase('Build: register variant'):
            from .registration import vehicle_parts
            # "Sell in the shop": priced from the units it is made of (registration.shop_offer).
            shop={'parts':vehicle_parts(result.get('metadata') or {})} if body.get('shop') else None
            registration=register_variant(self.index,result,staging,new_id,name,**({'shop':shop} if shop else {}))
        set_campaign(staging,new_id,data.get('campaign_name') or Path(data['source_path']).stem)
        describe_addon(staging)
        write_stack(staging,self.index,(rebuilt or {}).get('skipped'))
        # Leftover parts go to the depot once the build is installed.
        leftovers=[]
        if recipient.get('eid') and not drafting and not editing and not (transform.get('gun_swap') or transform.get('gun_swaps')) and (not supporting or metadata.get('support_vehicle',{}).get('removed_mount')):
            leftovers.append({'part':'turret','template':recipient['template'],'source':recipient['eid'],
                              'label':'Torreta de '+(recipient.get('label') or recipient['template'])})
        if donor.get('eid') and not drafting and not (transform.get('gun_swap') or transform.get('gun_swaps')) and transform.get('assembly_scope')!='complete':
            leftovers.append({'part':'hull','template':donor['template'],'source':donor['eid'],
                              'label':'Chasis de '+(donor.get('label') or donor['template'])})
        depot_change={'add':leftovers,'take':[item['depot'] for item in (recipient,donor) if item.get('depot')]}
        donor_hulls=set()
        complete_donors={row.get('donor_eid') for row in spec.get('turrets',[]) if row.get('assembly_scope')=='complete'} if drafting else set()
        for removed in metadata.get('removed_modules',[]) if drafting else []:
            mount=removed['removed_mount']
            leftovers.append({'part':'turret','template':removed.get('template') or recipient['template'],'source':recipient['eid'],'mount':removed.get('source_mount') or mount,
                'label':'Torreta de '+(recipient.get('label') or recipient['template'])+' ('+mount+')'})
        for turret in spec.get('turrets',[]) if drafting else []:
            if turret.get('native'):continue
            # Each replaced chassis turret becomes a depot part that remembers
            # which mount it was; each donor leaves its chassis once.
            if turret.get('donor_depot'):depot_change['take'].append(turret['donor_depot'])
            mount=turret.get('recipient_mount') or 'turret'
            if not mount.startswith('@free') and mount not in {r.get('design_mount',r['removed_mount']) for r in metadata.get('removed_modules',[])}:
                leftovers.append({'part':'turret','template':recipient['template'],'source':recipient['eid'],
                    **({} if mount=='turret' else {'mount':mount}),
                    'label':'Torreta de '+(recipient.get('label') or recipient['template'])+('' if mount=='turret' else ' ('+mount+')')})
            eid=turret.get('donor_eid')
            if eid and eid not in complete_donors and eid not in donor_hulls and not any(r.get('donor_eid')==eid for r in spec.get('guns',[])):
                donor_hulls.add(eid)
                leftovers.append({'part':'hull','template':turret['donor_template'],'source':eid,'label':'Chasis de '+turret['donor_template']})
        self.closed()
        with self.phase('Build: prepare verified transaction'):
            transaction=prepare_transaction(operation,snapshot,plan,addon_directory=staging)
        response={'transaction':transaction,'compilation':result,'registration':registration,'plan':plan,
                  'addon_id':addon_id,'new_template':new_id,'operation_dir':str(operation),'source_path':data['source_path'],
                  'rebuilt_for_new_mods':rebuilt,'depot_change':depot_change,'name':name}
        self.last_result=response
        (operation/'result.json').write_text(json.dumps(serial(response),ensure_ascii=False,indent=2),encoding='utf-8')
        metadata=result.get('metadata') or {}
        self.record_history('built',{'vehicle':new_id,'name':name,'save':Path(data['source_path']).name,'operation':operation.name,
            'recipient':recipient.get('template'),'recipient_eid':recipient.get('eid'),'donor':donor.get('template'),'donor_eid':donor.get('eid'),
            'transform':metadata.get('transform'),'crew':metadata.get('crew_roles'),
            'warnings':[d.get('code') for d in result.get('diagnostics',[]) if d.get('level')=='warning'],'depot':depot_change})
        return response

    @staticmethod
    def decode_portrait(images):
        """Validate complete PNGs, including CRCs, image data and all four states.

        No image library is needed. The legal PNG bit depths and Adam7 passes
        are accepted; dimensions, inflate size and row filter bytes are bounded.
        A signature and a forged IHDR alone must never reach the game.
        """
        if not isinstance(images,list) or len(images)!=4: raise ValueError('Retrato inválido.')
        decoded=[]
        for image in images:
            prefix='data:image/png;base64,'
            if not isinstance(image,str) or not image.startswith(prefix) or len(image)>240_000: raise ValueError('Retrato inválido.')
            try:
                raw=base64.b64decode(image[len(prefix):],validate=True)
                if raw[:8]!=b'\x89PNG\r\n\x1a\n': raise ValueError()
                cursor=8; header=None; compressed=[]; palette=False; ended=False; after_data=False
                while cursor<len(raw):
                    if cursor+12>len(raw): raise ValueError()
                    length=struct.unpack_from('>I',raw,cursor)[0]; kind=raw[cursor+4:cursor+8]
                    end=cursor+12+length
                    if end>len(raw) or not re.fullmatch(b'[A-Za-z]{4}',kind): raise ValueError()
                    payload=raw[cursor+8:cursor+8+length]
                    if zlib.crc32(kind+payload)&0xffffffff!=struct.unpack_from('>I',raw,cursor+8+length)[0]: raise ValueError()
                    if header is None and kind!=b'IHDR': raise ValueError()
                    if kind==b'IHDR':
                        if cursor!=8 or length!=13: raise ValueError()
                        header=struct.unpack('>IIBBBBB',payload)
                        width,height,depth,color,compression,filtering,interlace=header
                        depths={0:{1,2,4,8,16},2:{8,16},3:{1,2,4,8},4:{8,16},6:{8,16}}
                        if (width,height)!=(112,140) or depth not in depths.get(color,set()) or compression or filtering or interlace not in (0,1): raise ValueError()
                    elif kind==b'PLTE':
                        if palette or compressed or color in (0,4) or not length or length%3 or length>768: raise ValueError()
                        if color==3 and length//3>2**depth: raise ValueError()
                        palette=True
                    elif kind==b'IDAT':
                        if after_data or (color==3 and not palette): raise ValueError()
                        compressed.append(payload)
                    elif kind==b'IEND':
                        if length or not compressed or end!=len(raw): raise ValueError()
                        ended=True; break
                    elif kind[0]&32==0:  # unknown critical chunk
                        raise ValueError()
                    if compressed and kind!=b'IDAT': after_data=True
                    cursor=end
                if not ended or header is None: raise ValueError()
                channels={0:1,2:3,3:1,4:2,6:4}[color]
                passes=[(0,0,1,1)] if not interlace else [(0,0,8,8),(4,0,8,8),(0,4,4,8),(2,0,4,4),(0,2,2,4),(1,0,2,2),(0,1,1,2)]
                rows=[]
                for x,y,step_x,step_y in passes:
                    pass_width=max(0,(width-x+step_x-1)//step_x)
                    pass_height=max(0,(height-y+step_y-1)//step_y)
                    if pass_width and pass_height:
                        rows.extend([1+(pass_width*channels*depth+7)//8]*pass_height)
                expected=sum(rows); inflater=zlib.decompressobj()
                pixels=inflater.decompress(b''.join(compressed),expected+1)
                if len(pixels)!=expected or not inflater.eof or inflater.unused_data or inflater.unconsumed_tail: raise ValueError()
                offset=0
                for row_size in rows:
                    if pixels[offset]>4: raise ValueError()
                    offset+=row_size
            except (ValueError,struct.error,zlib.error) as error:
                raise ValueError('Retrato inválido.') from error
            decoded.append(raw)
        return decoded

    @staticmethod
    def portrait_files(new_id,images):
        if not isinstance(new_id,str) or not re.fullmatch(r'mw_[A-Za-z0-9_-]+',new_id):
            raise ValueError('Vehículo del taller inválido.')
        decoded=AppState.decode_portrait(images)
        record={'schema_version':1,'id':new_id,'source':'rendered_model',
                'sha256':[hashlib.sha256(raw).hexdigest() for raw in decoded]}
        files={f'resource/interface/scene/portrait_squad/{new_id}_{variant:02d}.png':raw for variant,raw in enumerate(decoded)}
        files[f'portrait_{new_id}.json']=json.dumps(record,ensure_ascii=False,indent=2).encode('utf-8')
        return files,record

    @staticmethod
    def write_portrait(staging,new_id,images):
        """Only staging: installed portraits use the guarded journal operation."""
        from .registration import portrait_geometry_hashes
        files,record=AppState.portrait_files(new_id,images)
        staging=Path(staging)
        record['geometry_sha256']=portrait_geometry_hashes(staging,new_id)
        files[f'portrait_{new_id}.json']=json.dumps(record,ensure_ascii=False,indent=2).encode('utf-8')
        for relative,raw in files.items():
            path=staging/relative
            path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(raw)

    def clear_portrait_previews(self):
        self.clear_gun_preview()
        for design in self.__dict__.pop('design_previews',{}).values():design['index'].close();design['temp'].cleanup()
        contexts,self.portrait_previews=self.portrait_previews,{}
        for context in contexts.values(): context['index'].close()

    def generated_vehicles(self):
        """Stored recipes of the shared addon; no campaign is edited or required."""
        from .registration import portrait_state
        from .transactions import _owned_addon
        addon=self.addon_root(); rows=[]; diagnostics=[]
        if not addon.is_dir(): return {'vehicles':rows,'diagnostics':diagnostics}
        self.owned_addon(addon)
        for path in sorted(addon.glob('assembly_*.json')):
            try:
                data=json.loads(path.read_text(encoding='utf-8')); identifier=data['id']; metadata=data['metadata']
                if not re.fullmatch(r'mw_[A-Za-z0-9_-]+',identifier) or path.name!=f'assembly_{identifier}.json':
                    raise ValueError('Vehículo del taller inválido.')
                manifest=addon/f'workshop_manifest_{identifier}.json'
                label=json.loads(manifest.read_text(encoding='utf-8')).get('display_name') if manifest.is_file() else None
                state=portrait_state(addon,identifier)
                rows.append({'id':identifier,'name':label or identifier,
                             'recipe':{key:metadata.get(key) for key in ('recipient','donor','transform','crew_slots')},
                             'portrait_source':state['source'],'has_rendered_portrait':state['source']=='rendered_model'})
            except (ValueError,KeyError,TypeError) as error:
                diagnostics.append({'code':'portrait_recipe_invalid','level':'warning','path':path.name,
                                    'message':'No se pudo leer la receta del vehículo. '+str(error)})
        return {'vehicles':rows,'diagnostics':diagnostics}

    def shop_listing(self):
        """Workshop vehicles and whether each is sold in the Conquest shop, with
        the price and research stage it gets. Reading it changes nothing."""
        from .registration import RegistrationError, _recipes, shop_offer, shop_prices, vehicle_parts
        addon=self.addon_root();rows=[]
        if not (addon/'mod.info').is_file():return {'vehicles':rows,'priced':False}
        # Reading only: applying (set_shop) verifies the addon is ours.
        recipes=_recipes(addon)
        cached=getattr(self,'_shop_prices',None)
        if self.index is not None and (cached is None or cached[0] is not self.index):
            cached=self._shop_prices=(self.index,shop_prices(self.index))  # read once per resource index
        prices=cached[1] if self.index is not None else None
        for manifest in sorted(addon.glob('workshop_manifest_*.json')):
            try:data=json.loads(manifest.read_text(encoding='utf-8'))
            except (OSError,ValueError):continue
            identifier=data.get('id')
            if identifier not in recipes:continue
            parts=vehicle_parts(recipes[identifier])
            row={'id':identifier,'name':data.get('display_name') or identifier,'on':bool(data.get('shop')),'parts':parts,
                 'campaign':data.get('campaign')}
            if self.index is not None:
                try:
                    offer=shop_offer(self.index,parts,recipes,prices=prices)
                    row.update(cost=offer['cost'],stage=offer['stage'],unpriced=offer['unpriced'])
                except RegistrationError as error:row['problem']=str(error)
            rows.append(row)
        return {'vehicles':rows,'priced':self.index is not None}

    def _publish_addon(self,operation,staging,original):
        """Replace the shared addon with a staged one, never a save. The current
        addon is archived in the operation folder and restored on failure; the
        next operation reindexes."""
        from .transactions import OWNERSHIP_FILE, _owned_addon, _tree_hashes
        addon=self.addon_root()
        if addon.is_dir() and _owned_addon(addon,ADDON_MOD)!=original:
            raise ValueError('El addon cambió mientras se preparaba la operación. Volvé a intentarlo.')
        previous=operation/'previous_addon'
        if addon.is_dir():shutil.copytree(long_path(addon),long_path(previous))
        try:
            shutil.rmtree(long_path(addon),ignore_errors=True)
            shutil.copytree(long_path(staging),long_path(addon))
            marker={'schema_version':1,'transaction_id':operation.name,'addon_mod':ADDON_MOD,'files':_tree_hashes(addon)}
            (addon/OWNERSHIP_FILE).write_text(json.dumps(marker,sort_keys=True),encoding='utf-8')
        except Exception:
            shutil.rmtree(long_path(addon),ignore_errors=True)
            if previous.is_dir():shutil.copytree(long_path(previous),long_path(addon))
            raise
        with self.lock:
            stale,self.index=self.index,None;self.previews.clear();self.__dict__.pop('checks',None);self.__dict__.pop('crew_models',None)
        self.stop_catalog_warmup()
        if stale:stale.close()
        self.mark_addon_published()
        self.prune_old_operations()
        return previous

    def creative(self,body):
        """Creative mode (Geofisika): build from any vehicles of the catalog,
        spending no unit of any campaign. The vehicle goes to the Conquest shop
        of its chassis army at the price of its parts; no save is written."""
        from .compiler import compile_assembly
        from .registration import register_variant, rebuild_registrations, recorded_stack, stack_signature, write_stack, describe_addon, vehicle_parts
        from .resources import ResourceIndex
        from .transactions import OWNERSHIP_FILE
        self.closed()
        if self.index is None:raise ValueError('Leé los recursos del juego primero.')
        problem=self.mod_stack_problem(include_generated=False)
        if problem:raise ValueError(problem)
        recipient,donor=body.get('recipient'),body.get('donor')
        for template in (recipient,donor):
            if not isinstance(template,str) or not template or not self.index.has_entity(template):
                raise ValueError('Elegí un chasis y una torreta del catálogo.')
        transform=copy.deepcopy(body.get('transform') or {})
        if not isinstance(transform,dict):raise ValueError('El diseño no es válido.')
        for key in ('assembly_draft','gun_swap','support_vehicle','remove_module'):
            # Those operations take their parts from a campaign (or are unsaved);
            # saved gun changes only name catalog models, like the chassis.
            transform.pop(key,None)
        name=str(body.get('name') or '').strip()[:100]
        fingerprint=hashlib.sha256(json.dumps({'hull':recipient,'module':donor,'transform':transform,'creative':True,
            'sources':self.index.sources},sort_keys=True).encode()).hexdigest()[:12]
        new_id='mw_'+fingerprint;existing=self.addon_root();attempt=1
        while self.index.has_entity(new_id) or (existing/'resource'/'entity'/'-vehicle'/'workshop'/new_id).exists():
            new_id='mw_'+hashlib.sha256(f'{fingerprint}:{attempt}'.encode()).hexdigest()[:12];attempt+=1
        operation=self.workspace/'operations'/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_creative_'+secrets.token_hex(3))
        staging=operation/ADDON_ID;original=None;rebuilt=None
        if existing.is_dir():
            original=self.owned_addon(existing)
            shutil.copytree(long_path(existing),long_path(staging),ignore=lambda directory,names:[OWNERSHIP_FILE] if OWNERSHIP_FILE in names else [])
            recorded=recorded_stack(staging)
            if recorded is None or recorded!=stack_signature(self.index) or self.mod_stack_problem():
                with self.phase('Creative: register every vehicle again (mods changed)'):
                    base=ResourceIndex(self.settings['game_root'],[root for root in self.settings['mod_roots'] if not is_own_mod(Path(root).name)]).build()
                    try:rebuilt=rebuild_registrations(base,staging)
                    finally:base.close()
        else:staging.mkdir(parents=True)
        compiled=operation/'compile'
        with self.phase('Creative: compile vehicle'):
            result=compile_assembly(self.index,recipient,donor,transform,compiled,new_id)
        for path in sorted(long_path(compiled).rglob('*')):
            if path.is_file():
                target=long_path(staging)/path.relative_to(long_path(compiled))
                if target.exists():raise ValueError(f'El ensamblaje generado choca con un archivo existente del addon: {target.relative_to(long_path(staging))}')
                target.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(path),target)
        shutil.rmtree(long_path(compiled))
        result['campaign_plan']={}
        name=name or (self.index.label_for(recipient)+' + '+self.index.label_for(donor))
        with self.phase('Creative: register for the shop'):
            registration=register_variant(self.index,result,staging,new_id,name,shop={'parts':vehicle_parts(result.get('metadata') or {})})
        describe_addon(staging);write_stack(staging,self.index,(rebuilt or {}).get('skipped'))
        game=self.closed() or {}
        previous=self._publish_addon(operation,staging,original)
        offer=(registration.get('shop') or {}).get('offer')
        self.record_history('creative',{'vehicle':new_id,'name':name,'recipient':recipient,'donor':donor,'offer':offer,
                                         'transform':(result.get('metadata') or {}).get('transform')})
        return {'new_template':new_id,'name':name,'offer':offer,'backup':str(previous) if previous.is_dir() else None,
                'reload_steps':self.reload_steps(game),'diagnostics':result.get('diagnostics',[])}

    def set_shop(self,body):
        """Put workshop vehicles up for sale in their army's Conquest shop, or
        take them out. Only the shared addon changes, never a save: the shop
        sells them in every campaign that uses the addon. The previous addon is
        archived in the operation folder and restored if publishing fails."""
        from .registration import SHOP_SHARE, _recipes, describe_addon, rebuild_registrations, vehicle_parts
        from .resources import ResourceIndex
        from .transactions import OWNERSHIP_FILE, _owned_addon, _tree_hashes
        changes=body.get('changes')
        if not isinstance(changes,dict) or not changes:raise ValueError('Elegí qué vehículos poner en la tienda o sacar de ella.')
        game=self.closed() or {}
        addon=self.addon_root()
        if not (addon/'mod.info').is_file():raise ValueError('Todavía no hay vehículos creados con el taller.')
        original=self.owned_addon(addon)
        recipes=_recipes(addon)
        for identifier in changes:
            if not isinstance(identifier,str) or not re.fullmatch(r'mw_[A-Za-z0-9_-]+',identifier) or identifier not in recipes:
                raise ValueError('Vehículo del taller inválido.')
        operation=self.workspace/'operations'/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_shop_'+secrets.token_hex(3))
        staging=operation/ADDON_ID
        shutil.copytree(long_path(addon),long_path(staging),ignore=lambda directory,names:[OWNERSHIP_FILE] if OWNERSHIP_FILE in names else [])
        for identifier,on in changes.items():
            path=staging/f'workshop_manifest_{identifier}.json'
            data=json.loads(path.read_text(encoding='utf-8'))
            if on:data['shop']={'parts':vehicle_parts(recipes[identifier]),'share':SHOP_SHARE}
            else:data.pop('shop',None)
            path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        roots=[root for root in self.settings.get('mod_roots',[]) if not is_own_mod(Path(root).name)]
        with self.phase('Shop: register vehicles again'):
            base=ResourceIndex(self.settings['game_root'],roots).build()
            try:registrations=rebuild_registrations(base,staging,identifiers=set(changes))
            finally:base.close()
        failed=[row for row in registrations.get('skipped',[]) if row.get('id') in changes]
        if failed:
            raise ValueError('No se pudo cambiar la tienda de: '+'; '.join(f"{row['id']} ({row.get('reason')})" for row in failed))
        describe_addon(staging)
        game=self.closed() or game
        previous=self._publish_addon(operation,staging,original)
        offers={}
        for identifier in changes:
            data=json.loads((addon/f'workshop_manifest_{identifier}.json').read_text(encoding='utf-8'))
            offers[identifier]=(data.get('shop') or {}).get('offer')
        self.record_history('shop',{'changes':changes,'offers':offers})
        return {'changes':changes,'offers':offers,'backup':str(previous),'reload_steps':self.reload_steps(game)}

    def _portrait_target(self,body):
        from .transactions import _owned_addon, _read_journal, verify_transaction
        identifier=body.get('id')
        if not isinstance(identifier,str) or not re.fullmatch(r'mw_[A-Za-z0-9_-]+',identifier):
            raise ValueError('Vehículo del taller inválido.')
        if body.get('staged'):
            result=self.last_result
            if result is None or result.get('new_template')!=identifier:
                raise ValueError('El vehículo preparado ya no está disponible. Construí de nuevo.')
            tx=result['transaction']; path,journal=_read_journal(tx.get('journal_path') or tx.get('journal'))
            if journal['state']!='prepared': raise ValueError('La operación preparada ya fue aplicada.')
            with self.phase('Portrait: verify prepared transaction'):
                verification=verify_transaction(path)
            addon=Path(journal['staged_addon']); hashes=verification['addon_hashes']
        else:
            addon=self.addon_root(); hashes=self.owned_addon(addon); path=None; journal=None
        recipe=addon/f'assembly_{identifier}.json'
        try: data=json.loads(recipe.read_text(encoding='utf-8'))
        except (OSError,ValueError) as error: raise ValueError('No se encontró la receta del vehículo.') from error
        if data.get('id')!=identifier: raise ValueError('La receta no corresponde al vehículo.')
        return addon,hashes,path,journal,data

    def portrait_preview(self,body):
        """Read the actual compiled full model, including chained/free assemblies.

        A separate resource index exposes the exact staged model and its textures
        without changing the editable chassis/module selection in the main UI.
        The token binds the rendered image to these verified addon files.
        """
        from .resources import ResourceIndex
        from .geometry import build_preview
        from .registration import portrait_state
        addon,hashes,path,journal,recipe=self._portrait_target(body)
        roots=[root for root in self.settings.get('mod_roots',[]) if not is_own_mod(Path(root).name)]
        with self.phase('Portrait: index resources'):
            current=self.index
            configured=[Path(p).resolve() for p in self.settings.get('mod_roots',[])]
            if (current is not None and current.built and current.game_root==Path(self.settings['game_root']).resolve()
                    and current.mod_roots==configured):
                index=current.with_overlay(addon,exclude_roots=[p for p in current.mod_roots if is_own_mod(p.name)])
            else:
                index=ResourceIndex(self.settings['game_root'],[*roots,str(addon)]).build()
        identifier=recipe['id']
        try:
            with self.phase('Portrait: read assembled geometry'):
                preview=build_preview(index,identifier,'full',include_inventory=True)
            if not preview.get('meshes'): raise ValueError('No se pudo cargar el modelo armado para el retrato.')
            # Omitting any mesh would make a partial vehicle look successful.
            if any(row.get('code') in ('mesh_unavailable','mounted_weapon_unavailable') for row in preview.get('diagnostics',[])):
                raise ValueError('El modelo armado tiene mallas que no se pudieron leer. Se conserva el retrato de respaldo.')
            manifest=addon/f'workshop_manifest_{identifier}.json'
            name=json.loads(manifest.read_text(encoding='utf-8')).get('display_name') if manifest.is_file() else identifier
        except Exception:
            index.close(); raise
        self.clear_portrait_previews()
        token=secrets.token_urlsafe(24)
        self.portrait_previews[token]={'index':index,'addon':str(addon),'hashes':hashes,
            'id':identifier,'staged':bool(body.get('staged')),'settings':json.dumps(self.settings,sort_keys=True),'created':time.time()}
        return {'id':identifier,'name':name or identifier,'preview':preview,'preview_token':token,
                'portrait_source':portrait_state(addon,identifier)['source'],'diagnostics':preview.get('diagnostics',[])}

    def save_portrait(self,body):
        """Backed-up, journaled, atomic file replacements; never touch a save.

        The ownership marker is published with the PNGs. On failure, restore
        only files whose contents still match this operation or their originals.
        A process interruption leaves the journal, backups and staged images for
        recovery, with the addon deliberately failing ownership verification.
        """
        from .transactions import (OWNERSHIP_FILE, TransactionError, _atomic_json, _file_hash,
                                   _no_symlink, _owned_addon, _write_file, verify_transaction)
        game=self.closed() or {}
        with self.phase('Portrait: encode images'):
            files,record=self.portrait_files(body.get('id'),body.get('images'))
        addon,hashes,tx_path,tx_journal,recipe=self._portrait_target(body)
        # The complete addon was just hashed by _portrait_target. Reuse that
        # verified appearance subset rather than rereading every model/texture.
        prefix=f"resource/entity/-vehicle/workshop/{recipe['id']}/"
        record['geometry_sha256']={name:digest for name,digest in hashes.items() if name.startswith(prefix)}
        files[f"portrait_{recipe['id']}.json"]=json.dumps(record,ensure_ascii=False,indent=2).encode('utf-8')
        context=self.portrait_previews.get(body.get('preview_token'))
        if (context is None or context['id']!=recipe['id'] or context['addon']!=str(addon)
                or context['staged']!=bool(body.get('staged')) or context['hashes']!=hashes
                or context['settings']!=json.dumps(self.settings,sort_keys=True) or time.time()-context['created']>1800):
            raise ValueError('La vista del retrato venció. Cargala de nuevo.')
        icons=body.get('icons')
        if icons is not None:
            # Battle icons of the same assembled model, laid over the chassis'
            # own icon plate (nation emblem strip), in the four native states.
            from .icons import ICON_KINDS, IconError, compose, decode_render, chassis_strips
            from .registration import _recipes, _native_template
            recipes=_recipes(addon)
            native=_native_template(recipes,recipe['id'],'recipient')
            if not isinstance(icons,dict) or set(icons)-set(ICON_KINDS): raise ValueError('Icono inválido.')
            try:
                for kind,(width,height) in ICON_KINDS.items():
                    if kind not in icons: continue
                    render=decode_render(icons[kind],width,height)
                    strips=chassis_strips(context['index'],[native],kind)
                    for variant,raw in enumerate(compose(kind,render,strips)):
                        files[f"resource/interface/scene/{kind}/{recipe['id']}_{variant:02d}.tga"]=raw
            except IconError as error:
                raise ValueError('Icono inválido.') from error
            record['icons']=sorted(kind for kind in icons if kind in ICON_KINDS)
            files[f"portrait_{recipe['id']}.json"]=json.dumps(record,ensure_ascii=False,indent=2).encode('utf-8')
        manifest=addon/f"workshop_manifest_{recipe['id']}.json"
        if not manifest.is_file(): raise ValueError('No se encontró el registro del vehículo.')
        metadata=json.loads(manifest.read_text(encoding='utf-8'))
        metadata['portrait']=record
        metadata['warnings']=[warning for warning in metadata.get('warnings',[]) if not warning.startswith('El retrato armado todavía')]
        files[manifest.name]=json.dumps(metadata,ensure_ascii=False,indent=2).encode('utf-8')
        operation=self.workspace/'operations'/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_portrait_'+secrets.token_hex(4))
        operation.mkdir(parents=True,exist_ok=False)
        _no_symlink(addon)
        before={}
        for relative,raw in files.items():
            destination=addon/relative; _no_symlink(destination)
            before[relative]=destination.read_bytes() if destination.is_file() else None
            if before[relative] is not None: _write_file(operation/'backup'/relative,before[relative])
            _write_file(operation/'stage'/relative,raw)
        if tx_path is None:
            marker=json.loads((addon/OWNERSHIP_FILE).read_text(encoding='utf-8'))
            marker['files']={relative:digest for relative,digest in hashes.items() if relative!=OWNERSHIP_FILE}
            marker['files'].update({relative:hashlib.sha256(raw).hexdigest() for relative,raw in files.items()})
            files[OWNERSHIP_FILE]=json.dumps(marker,sort_keys=True).encode('utf-8')
            before[OWNERSHIP_FILE]=(addon/OWNERSHIP_FILE).read_bytes()
            _write_file(operation/'backup'/OWNERSHIP_FILE,before[OWNERSHIP_FILE])
            _write_file(operation/'stage'/OWNERSHIP_FILE,files[OWNERSHIP_FILE])
        else:
            tx_original=tx_path.read_bytes()
            _write_file(operation/'backup'/'prepared_transaction.json',tx_original)
        journal={'schema_version':1,'kind':'portrait','id':recipe['id'],'addon_mod':ADDON_MOD,'addon':str(addon),
                 'operation_dir':str(operation),'state':'prepared','staged':bool(body.get('staged')),
                 'transaction_journal':str(tx_path) if tx_path else None,'published':[],
                 'before':{relative:hashlib.sha256(raw).hexdigest() if raw is not None else None for relative,raw in before.items()},
                 'after':{relative:hashlib.sha256(raw).hexdigest() for relative,raw in files.items()}}
        journal_path=operation/'journal.json'; _atomic_json(journal_path,journal)
        def replace(relative,raw):
            target=addon/relative; target.parent.mkdir(parents=True,exist_ok=True); _no_symlink(target)
            temporary=target.parent/('.'+target.name+'.'+secrets.token_hex(6)+'.tmp')
            try:
                _write_file(temporary,raw); os.replace(temporary,target)
            finally:
                temporary.unlink(missing_ok=True)
        try:
            self.closed()
            # Recheck ownership after backup and staging, before the first write.
            if tx_path is None:
                if _owned_addon(addon,ADDON_MOD)!=hashes: raise TransactionError('Addon changed while preparing portrait')
            else:
                with self.phase('Portrait: verify before publishing'):verify_transaction(tx_path)
            for relative,raw in files.items():
                journal.update(state='publishing',next=relative); _atomic_json(journal_path,journal)
                replace(relative,raw); journal['published'].append(relative); _atomic_json(journal_path,journal)
            if tx_path is not None:
                tx_journal['addon_hashes'].update({relative:digest for relative,digest in journal['after'].items()})
                _atomic_json(tx_path,tx_journal)
                with self.phase('Portrait: verify updated files'):verify_transaction(tx_path)
            else: _owned_addon(addon,ADDON_MOD)
            journal.update(state='committed',next=None); _atomic_json(journal_path,journal)
        except Exception as error:
            conflicts=[]
            for relative in reversed(list(files)):
                target=addon/relative
                current=_file_hash(target) if target.is_file() else None
                if current not in (journal['before'][relative],journal['after'][relative]):
                    conflicts.append(relative); continue
                if before[relative] is None: target.unlink(missing_ok=True)
                elif current!=journal['before'][relative]: replace(relative,before[relative])
            if tx_path is not None:
                current=tx_path.read_bytes()
                if current==tx_original or json.loads(current)==tx_journal:
                    original=json.loads(tx_original); _atomic_json(tx_path,original)
                else: conflicts.append('prepared_transaction.json')
            journal.update(state='recovery_required' if conflicts else 'rolled_back',error=str(error),conflicts=conflicts)
            _atomic_json(journal_path,journal)
            if conflicts: raise TransactionError('Portrait rollback found externally changed files: '+', '.join(conflicts)) from error
            raise
        finally:
            self.clear_portrait_previews()
        if tx_path is None:
            self.mark_addon_published()
            with self.lock:
                stale,self.index=self.index,None; self.previews.clear(); self.__dict__.pop('checks',None)
            self.stop_catalog_warmup()
            if stale: stale.close()
        return {'id':recipe['id'],'portrait_source':'rendered_model','backup':str(operation/'backup'),
                'journal_path':str(journal_path),'reload_steps':self.reload_steps(game)}

    @staticmethod
    def reload_steps(game,activated=False):
        """With the game open in the menu, Extras > Apply remounts every mod in
        the same process (verified in game.log); new files stay invisible until
        then, and loading the campaign earlier crashes ("File not found")."""
        if not game.get('running'): return []
        from .addon_readiness import LOCAL_MOD_HELP
        return [LOCAL_MOD_HELP,
                'Si ya estaba activo, desactivalo y aplicá; después volvé a activarlo y aplicá.',
                'Volvé al taller y pulsá «Comprobar carga del mod». No cargues ni guardes la campaña hasta que se confirme.']

    GLOBAL_FILES=('set/interaction_entity/entity.set','set/multiplayer/units/roster_conquest.set')

    def mod_stack_problem(self,*,include_generated=True):
        """A file the active mods include but none provides, typically because a
        mod depends on another the game switches off by version. The game then
        closes at start with or without the workshop; say so plainly instead."""
        from .cancellation import JobCancelled
        from .registration import _load_include_graph
        exists=getattr(self.index,'exists',None)
        if exists is None: return None
        source=self.index.registration_sources() if hasattr(self.index,'registration_sources') else self.index
        for start in self.GLOBAL_FILES:
            if not exists(start): continue
            try:
                _load_include_graph(source,start)
            except FileNotFoundError as error:
                match=re.search(r"reference '([^']+)' from (\S+)",str(error))
                missing,owner=(match.group(1),match.group(2)) if match else (str(error),start)
                try: by=source.resolve(owner).origin
                except Exception: by='?'
                disabled=', '.join(m['name'] for m in getattr(self.index,'disabled_mods',[]))
                return (f'Tus mods activos están incompletos: «{missing}» no existe y lo pide «{by}» ({owner}). '
                        'El juego se va a cerrar al iniciar, con o sin el taller.'
                        +(f' Probablemente falta {disabled}, que el juego desactiva por la versión.' if disabled else '')
                        +' Desactivá o actualizá ese mod.')
            except JobCancelled:
                raise
            except Exception:  # a check that only warns must never stop opening a campaign
                logging.exception('Mod stack check skipped for %s',start)
        if include_generated and source is not self.index:
            for start in self.GLOBAL_FILES:
                if not exists(start):continue
                try:_load_include_graph(self.index,start,generated=True)
                except FileNotFoundError:
                    return ('El addon local «Cursed Workshop - Built Vehicles» conserva referencias de otra combinación de mods. '
                            'Pulsá «Actualizar vehículos» para reconstruirlo con los mods actuales. Se conserva una copia de recuperación.')
                except JobCancelled:raise
                except Exception:logging.exception('Generated registration check skipped for %s',start)
        return None

    LOW_FREE_BYTES=2*1024**3

    def make_room(self,save_path):
        """Free the space of old operation copies before writing. A nearly full
        drive is only noted in the log: the player decides, the build goes on."""
        self.prune_old_operations()
        checked=set()
        for path in (self.workspace,Path(save_path).parent,self.addon_root().parent):
            try:
                anchor=Path(path).resolve().anchor or str(path)
                if anchor in checked: continue
                checked.add(anchor)
                free=shutil.disk_usage(anchor).free
            except OSError:
                continue
            if free<self.LOW_FREE_BYTES:
                logging.warning('Low disk space on %s: %.1f GB free',anchor,free/1024**3)

    def prune_old_operations(self):
        """Free the disk space of old addon copies; never fails an operation."""
        from .transactions import prune_operations
        try:
            result=prune_operations(self.workspace)
            if result['pruned']:
                logging.info('Pruned %d old operations, freed %.1f MB',len(result['pruned']),result['freed_bytes']/2**20)
            return result
        except Exception as error:  # cleanup is best effort
            logging.warning('Could not prune old operations: %s',error)
            return None

    def install(self,body):
        from .transactions import commit_transaction
        from .registration import validate_dependencies
        from .resources import ResourceIndex
        game=self.closed() or {}
        if self.last_result is None: raise ValueError('Construí el vehículo primero.')
        result=self.last_result; source=Path(result['source_path'])
        self.make_room(source)
        # The opened save itself is updated: one campaign, every build in it.
        # Its verified original stays in the operation's backup folder.
        save_destination=source
        addon_destination=self.addon_root()
        tx=result['transaction']
        journal=tx.get('journal_path') or tx.get('journal')
        if not journal: raise ValueError('No se encontró el registro de la operación.')
        with self.phase('Install: index and validate resources'):
            fresh=ResourceIndex(self.settings['game_root'],self.settings['mod_roots']).build()
            try: validate_dependencies(fresh,result['registration'])
            finally: fresh.close()
        with self.phase('Install: publish addon and campaign'):
            from .addon_readiness import preserve_campaign
            recovery=preserve_campaign(self.workspace,source,tx['save_path'],tx['save_hash'])
            self.mark_addon_published(recovery,pending=True)
            installed=commit_transaction(journal,addon_destination=addon_destination,save_destination=save_destination,
                                         allow_install=True,in_place=True,phase=self.phase)
        self.mark_addon_published(recovery)
        change=result.get('depot_change') or {}
        depot=Depot(self.workspace).apply(source,change.get('add',[]),change.get('take',[]))
        self.prune_old_operations()
        # The shared addon's files just changed: the resource index and model
        # previews describe the old ones, so the next operation reindexes.
        with self.lock:
            stale,self.index=self.index,None; self.previews.clear(); self.__dict__.pop("checks",None); self.__dict__.pop("crew_models",None); self.last_result=None
        self.stop_catalog_warmup()
        if stale: stale.close()
        # Activate the shared addon after the campaign's own mods; the player
        # no longer has to touch the mod list.
        activation=self.prepare_addon_activation(source,game)
        from .addon_readiness import LOCAL_MOD_HELP, WAIT_MESSAGE
        instructions=WAIT_MESSAGE+'\n'+LOCAL_MOD_HELP
        if activation:
            instructions='La activación del mod local quedó preparada para el próximo inicio del juego.\n'+instructions
        steps=self.reload_steps(game,bool(activation and activation.get('changed')))
        self.record_history('installed',{'vehicle':result.get('new_template'),'name':result.get('name'),'save':save_destination.name,
                                         'operation':Path(result['operation_dir']).name})
        return {'installation':installed,'addon_path':str(addon_destination),'save_path':str(save_destination),
                'activation':activation,'instructions':instructions,'reload_steps':steps,'depot':depot,'name':result.get('name'),
                'new_template':result.get('new_template'),'backup':str(Path(result['operation_dir'])),
                'addon_readiness':self.addon_readiness(game),'recovery_backup':recovery}

    def repair(self,body):
        """Rebuild only workshop vehicles owned in the explicitly open save.

        Compiler fixes then reach vehicles generated by older versions. IDs
        stay the same, so campaign saves keep working; the previous addon is
        archived in the workspace before it is replaced.
        """
        from .compiler import compile_assembly
        from .registration import rebuild_registrations, describe_addon
        from .resources import ResourceIndex
        from .transactions import OWNERSHIP_FILE, _owned_addon, _tree_hashes
        if self.report_save() is None:
            raise ValueError('Seleccioná primero una campaña.')
        snapshot=self.snapshot
        expected=getattr(snapshot,'source_hash',None)
        if expected and (hashlib.sha256(Path(snapshot.source_path).read_bytes()).hexdigest()!=expected
                         or body.get('source_hash',expected)!=expected):
            raise ValueError('La partida cambió. Volvé a abrirla antes de actualizar los vehículos.')
        selected={unit['template'] for unit in snapshot.to_dict()['units']
                  if unit.get('kind')=='vehicle' and unit.get('owned',True)
                  and re.fullmatch(r'mw_[A-Za-z0-9_-]+',unit.get('template',''))}
        if not selected:
            return {'rebuilt':[],'skipped':[],'backup':None,'labels':{},'reload_steps':[]}
        self.closed()
        self.clear_portrait_previews()
        addon=self.addon_root()
        if not (addon/'mod.info').is_file(): raise ValueError('Todavía no hay vehículos creados con el taller.')
        original_hashes=self.owned_addon(addon)
        operation=self.workspace/'operations'/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_repair_'+secrets.token_hex(3))
        staging=operation/ADDON_ID
        shutil.copytree(long_path(addon),long_path(staging),ignore=lambda directory,names:[OWNERSHIP_FILE] if OWNERSHIP_FILE in names else [])
        roots=[root for root in self.settings.get('mod_roots',[]) if not is_own_mod(Path(root).name)]
        base=ResourceIndex(self.settings['game_root'],roots).build()
        rebuilt,skipped=[],[]
        # What each vehicle is and how its rebuild went, for the build history:
        # vehicles made before the history existed are recorded here too.
        details={}
        try:
            recipes={}
            for recipe in sorted(staging.glob('assembly*.json')):
                if recipe.name!='assembly.json' and recipe.name not in {f'assembly_{identifier}.json' for identifier in selected}:continue
                data=json.loads(recipe.read_text(encoding='utf-8')); identifier=data.get('id'); metadata=data.get('metadata',{})
                if identifier not in selected:continue
                details[identifier or recipe.name]={'vehicle':identifier or recipe.name,'recipient':metadata.get('recipient'),
                    'donor':metadata.get('donor'),'built_with':data.get('tool_version'),'transform':metadata.get('transform'),
                    'crew':metadata.get('crew_roles')}
                if not identifier or not metadata.get('recipient') or not metadata.get('donor'):
                    skipped.append({'id':identifier or recipe.name,'reason':'receta incompleta'}); continue
                recipes[identifier]=(recipe,metadata)
            # Vehicles built from another workshop vehicle are rebuilt after it,
            # against its updated files (staging indexed as a mod), level by level.
            from .draft import recipe_dependencies
            parents={identifier:recipe_dependencies(metadata)&set(recipes) for identifier,(_,metadata) in recipes.items()}
            done=set()
            while len(done)<len(recipes):
                level=[identifier for identifier in sorted(recipes) if identifier not in done and parents[identifier]<=done]
                if not level:
                    for identifier in sorted(set(recipes)-done):
                        skipped.append({'id':identifier,'reason':'depende de sí mismo; se conserva tal cual'})
                    break
                chained=any(any(value.startswith('mw_') for value in recipe_dependencies(recipes[identifier][1]))
                            for identifier in level)
                with self.phase('Rebuild: index updated recipe parents'):
                    index=base.with_overlay(staging) if chained else base
                try:
                    for identifier in level:
                        done.add(identifier)
                        recipe,metadata=recipes[identifier]
                        compiled=operation/'compile'/identifier
                        try:
                            result=compile_assembly(index,metadata['recipient'],metadata['donor'],metadata.get('transform') or {},compiled,identifier)
                        except Exception as error:
                            skipped.append({'id':identifier,'reason':str(error)}); continue
                        before,after=metadata.get('crew_slots'),result['metadata'].get('crew_slots')
                        if before is not None and after!=before:
                            # Rebuilt anyway: a kept old version may crash the game. A
                            # crew larger than the new seats goes to the reserve when
                            # its campaign is opened (reconcile_crews).
                            details[identifier]['crew_slots']=[before,after]
                        target=staging/'resource'/'entity'/'-vehicle'/'workshop'/identifier
                        shutil.rmtree(long_path(target),ignore_errors=True)
                        shutil.copytree(long_path(compiled/'resource'/'entity'/'-vehicle'/'workshop'/identifier),long_path(target))
                        recipe.unlink()
                        (staging/f'assembly_{identifier}.json').write_text(json.dumps(serial(result),ensure_ascii=False,indent=2),encoding='utf-8')
                        rebuilt.append(identifier)
                        details[identifier].update(crew=result['metadata'].get('crew_roles'),
                            warnings=[d.get('code') for d in result.get('diagnostics',[]) if d.get('level')=='warning'],
                            fixes=sorted({d.get('code') for d in result.get('diagnostics',[]) if d.get('code') in
                                ('seat_rebased','orphan_slot_removed','boarding_clip_restored','rider_door_rebased')}))
                finally:
                    if index is not base: index.close()
            registrations=rebuild_registrations(base,staging,identifiers=set(rebuilt))
            describe_addon(staging)
        finally:
            base.close()
        # Publish: archive the current addon, then replace it; restore on failure.
        game=self.closed() or {}
        if (self.snapshot is not snapshot or
                (expected and hashlib.sha256(Path(snapshot.source_path).read_bytes()).hexdigest()!=expected)):
            raise ValueError('La campaña seleccionada cambió. Volvé a actualizar los vehículos.')
        if _owned_addon(addon,ADDON_MOD)!=original_hashes:
            raise ValueError('El addon cambió durante la regeneración. Volvé a cargarlo antes de actualizar.')
        previous=operation/'previous_addon'
        shutil.copytree(long_path(addon),long_path(previous))
        try:
            shutil.rmtree(long_path(addon))
            shutil.copytree(long_path(staging),long_path(addon))
            marker={'schema_version':1,'transaction_id':operation.name,'addon_mod':ADDON_MOD,'files':_tree_hashes(addon)}
            (addon/OWNERSHIP_FILE).write_text(json.dumps(marker,sort_keys=True),encoding='utf-8')
        except Exception:
            shutil.rmtree(long_path(addon),ignore_errors=True); shutil.copytree(long_path(previous),long_path(addon)); raise
        with self.lock:
            stale,self.index=self.index,None; self.previews.clear(); self.__dict__.pop("checks",None); self.__dict__.pop("crew_models",None)
        self.stop_catalog_warmup()
        if stale: stale.close()
        labels={}
        for manifest in addon.glob('workshop_manifest_*.json'):
            try:
                data=json.loads(manifest.read_text(encoding='utf-8'))
                if data['id'] in selected:labels[data['id']]=data.get('display_name') or data['id']
            except (ValueError,KeyError): continue
        self.prune_old_operations()
        reasons={str(item.get('id')):item.get('reason') for item in skipped if isinstance(item,dict)}
        for identifier,row in sorted(details.items()):
            row.update(name=labels.get(identifier),status='rebuilt' if identifier in rebuilt else 'kept',
                       **({'reason':reasons[identifier]} if identifier in reasons else {}))
            self.record_history('updated_vehicle',row)
        self.record_history('updated',{'rebuilt':rebuilt,'skipped':[str(x) for x in skipped+registrations.get('skipped',[])]})
        self.mark_addon_published()
        crew=[]
        if self.snapshot is not None and any(row.get('crew_slots') for row in details.values()):
            # The open campaign is fitted now (opening it fits its crews); any
            # other campaign when it is opened.
            try:crew=self.load_save({'path':str(self.snapshot.source_path)})['campaign'].get('crew_fitted') or []
            except Exception as error:  # the vehicles are already updated; opening the campaign again fits it
                logging.warning('Campaign not reopened after updating vehicles: %s',error)
        return {'rebuilt':rebuilt,'skipped':skipped+registrations.get('skipped',[]),'backup':str(previous),'labels':labels,
                'reload_steps':self.reload_steps(game),'crew_fitted':crew}

class WorkshopServer(ThreadingHTTPServer):
    request_queue_size=128


class Handler(BaseHTTPRequestHandler):
    server_version='CursedWorkshop'
    def log_message(self,fmt,*args): logging.info(fmt,*args)
    @property
    def state(self): return self.server.state

    def send_json(self,body,status=200):
        data=json.dumps(serial(body),ensure_ascii=False,allow_nan=False).encode('utf-8')
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff'); self.end_headers(); self.wfile.write(data)

    def authorized(self):
        host=self.headers.get('Host','')
        allowed={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
        if host not in allowed: raise PermissionError('Host local incorrecto.')
        origin=self.headers.get('Origin')
        if origin and origin not in {'http://'+h for h in allowed}: raise PermissionError('Origen incorrecto.')
        if self.path.startswith('/api/'): self.server.last_seen=time.monotonic()

    def do_GET(self):
        try:
            self.authorized()
            parsed=urlparse(self.path); query=parse_qs(parsed.query); path=parsed.path
            if path=='/api/session': return self.send_json({'token':self.state.token,'version':__version__})
            if path.startswith('/api/'):
                if self.headers.get('X-Workshop-Token')!=self.state.token: raise PermissionError('Sesión inválida.')
                if path=='/api/config': return self.send_json(self.state.config())
                if path=='/api/ui-preferences': return self.send_json(self.state.ui_preferences())
                if path=='/api/diagnostic': return self.send_json(self.state.diagnostic())
                if path=='/api/status':
                    game=self.state.settings.get('game_root') or None
                    status=game_state(game)
                    return self.send_json({'game_processes':running_game_processes(game),'game':status,'linux':running_under_wine(),
                                           'addon_readiness':self.state.addon_readiness(status)})
                if path=='/api/job':
                    with self.state.lock: job=dict(self.state.jobs.get(query.get('id',[''])[0],{}))
                    return self.send_json(job,200 if job else 404)
                if path=='/api/catalog':
                    if self.state.index is None: return self.send_json({'units':[],'indexed':False})
                    # Vehicles only, uncapped: with MACE the ~5,500 infantry breeds
                    # filled a 2,000-row cap sorted by kind and no tank reached the list.
                    units=[row for row in self.state.index.list_units(search=query.get('search',[''])[0]) if row['kind']!='infantry']
                    return self.send_json({'units':units,'indexed':True})
                if path=='/api/unit-image':
                    try:data=self.state.unit_image(query.get('template',[''])[0])
                    except FileNotFoundError:
                        self.send_response(404);self.send_header('Content-Length','0');self.end_headers();return
                    self.send_response(200);self.send_header('Content-Type','image/png');self.send_header('Content-Length',str(len(data)))
                    self.send_header('Cache-Control','private, max-age=300');self.send_header('X-Content-Type-Options','nosniff');self.end_headers()
                    self.wfile.write(data);return
                if path=='/api/campaign': return self.send_json(self.state.campaign_data() if self.state.snapshot else {})
                if path=='/api/generated-vehicles': return self.send_json(self.state.generated_vehicles())
                if path=='/api/shop': return self.send_json(self.state.shop_listing())
                if path=='/api/crew-model': return self.send_json(self.state.crew_model(query.get('pose',[''])[0]))
                if path=='/api/commander-mg': return self.send_json(self.state.commander_catalog())
                if path=='/api/texture':
                    data=self.state.texture(query.get('mtl',[''])[0],query.get('portrait_token',[None])[0])
                    if data is None:
                        self.send_response(204); self.send_header('Cache-Control','private, max-age=3600'); self.end_headers(); return
                    self.send_response(200); self.send_header('Content-Type','image/png'); self.send_header('Content-Length',str(len(data)))
                    self.send_header('Cache-Control','private, max-age=3600'); self.send_header('X-Content-Type-Options','nosniff'); self.end_headers()
                    return self.wfile.write(data)
                return self.send_json({'error':'Ruta inexistente'},404)
            relative=unquote(path).lstrip('/') or 'index.html'
            target=(WEB_ROOT/relative).resolve()
            if not target.is_relative_to(WEB_ROOT.resolve()) or not target.is_file(): return self.send_json({'error':'Archivo inexistente'},404)
            data=target.read_bytes(); self.send_response(200); self.send_header('Content-Type',mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
            self.send_header('Content-Length',str(len(data))); self.send_header('Cache-Control','no-cache'); self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers(); self.wfile.write(data)
        except ConnectionError:
            # A superseded read-only preview can close its socket normally.
            # Never report it as a compiler failure or send a second response.
            self.close_connection=True
        except PermissionError as exc: self.send_json({'error':str(exc)},403)
        except Exception as exc:
            self.state.record_error(exc,'HTTP GET')
            logging.exception('GET failed'); self.send_json({'error':str(exc)},400)

    def do_POST(self):
        try:
            self.authorized()
            if urlparse(self.path).path=='/api/bye':
                # The page is closing (browser mode). A beacon cannot carry headers.
                length=int(self.headers.get('Content-Length','0'))
                body=json.loads(self.rfile.read(length)) if 0<length<4096 else {}
                if isinstance(body,dict) and body.get('token')==self.state.token: self.server.bye=time.monotonic()
                self.send_response(204); self.end_headers(); return
            if self.headers.get('X-Workshop-Token')!=self.state.token: raise PermissionError('Sesión inválida.')
            length=int(self.headers.get('Content-Length','0')); route=urlparse(self.path).path
            limit=24*1024*1024 if route=='/api/screenshot' else 1024*1024  # a PNG of the 3D view
            if length<=0 or length>limit: raise ValueError('Tamaño de solicitud inválido.')
            body=json.loads(self.rfile.read(length))
            if route=='/api/ui-preferences':
                if not isinstance(body,dict): raise ValueError('Invalid interface preferences.')
                return self.send_json(self.state.ui_preferences(body))
            if route=='/api/cancel-job':return self.send_json(self.state.cancel_job(body))
            actions={'/api/configure':('Leer recursos',self.state.configure),'/api/load-save':('Abrir campaña',self.state.load_save),
                     '/api/preview':('Cargar modelo 3D',self.state.preview),'/api/free-anchor':('Calcular apoyo',self.state.free_anchor),'/api/generate':('Crear mod y campaña',self.state.generate),
                     '/api/install':('Instalar mod y copia',self.state.install),
                     '/api/portrait-preview':('Cargar modelo armado para el retrato',self.state.portrait_preview),
                     '/api/portrait':('Guardar retrato del modelo armado',self.state.save_portrait),
                     '/api/repair':('Actualizar vehículos',self.state.repair),'/api/check':('Revisar compatibilidad',self.state.check),
                     '/api/edit-source':('Abrir vehículo para editar',self.state.edit_source)}
            actions['/api/shop']=('Actualizar la tienda',self.state.set_shop)
            actions['/api/creative']=('Construir en modo creativo',self.state.creative)
            actions['/api/commander-mg-preview']=('Cargar montura de comandante',self.state.commander_preview)
            actions['/api/commander-catalog']=('Read commander weapons and supports',lambda body:self.state.commander_catalog())
            actions['/api/gun-catalog']=('Leer cañones disponibles',self.state.gun_catalog)
            actions['/api/support-catalog']=('Leer equipos de apoyo',self.state.support_catalog)
            actions['/api/armor-catalog']=('Leer blindajes disponibles',self.state.armor_catalog)
            actions['/api/rider-catalog']=('Leer MG de pasajero disponibles',self.state.rider_catalog)
            actions['/api/rider-preview']=('Cargar MG de pasajero',self.state.rider_preview)
            actions['/api/passenger-catalog']=('Leer poses de pasajeros',self.state.passenger_catalog)
            actions['/api/remove-vehicle']=('Quitar de la campaña',self.state.remove_vehicle)
            actions['/api/store-vehicle']=('Quitar de la campaña',self.state.store_vehicle)
            actions['/api/restore-stored-vehicle']=('Devolver a la campaña',self.state.restore_stored_vehicle)
            actions['/api/recover-stored-vehicle']=('Recuperar traslado',self.state.recover_stored_vehicle)
            actions['/api/smoke-preview']=('Cargar lanzador de humo',self.state.smoke_preview)
            actions['/api/armor-preview']=('Cargar pieza de blindaje',self.state.armor_preview)
            actions['/api/support-preview']=('Cargar vehículo de apoyo',self.state.support_preview)
            actions['/api/gun-preview']=('Cargar cañón intercambiable',self.state.gun_preview)
            actions['/api/design-preview']=('Cargar diseño del vehículo',self.state.design_preview_data)
            actions['/api/design-catalog']=('Leer armas del diseño',self.state.design_catalog)
            if route in actions:
                label,action=actions[route]
                lane=route if route in ('/api/check','/api/commander-mg-preview','/api/gun-preview','/api/design-preview','/api/design-catalog') else None
                cancellable=route in ('/api/preview','/api/free-anchor','/api/edit-source','/api/gun-catalog','/api/commander-catalog',
                    '/api/support-catalog','/api/armor-catalog','/api/smoke-preview','/api/armor-preview','/api/support-preview',
                    '/api/rider-catalog','/api/rider-preview','/api/passenger-catalog')
                return self.send_json(self.state.job(label,lambda:action(body),lane=lane,cancellable=cancellable),202)
            if route=='/api/screenshot':
                return self.send_json(self.state.screenshot(body))
            if route=='/api/error-report':
                return self.send_json(self.state.error_report(body))
            if route=='/api/pick':
                args=[sys.executable,'--pick',body.get('kind','file')] if getattr(sys,'frozen',False) else [sys.executable,'-m','workshop.picker',body.get('kind','file')]
                result=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',env={**os.environ,'PYTHONUTF8':'1'},check=True)
                return self.send_json({'path':result.stdout.strip()})
            if route=='/api/shutdown':
                self.send_json({'ok':True})
                window=getattr(self.server,'window',None)
                # In the desktop window, closing it ends the program and the server.
                threading.Thread(target=window.destroy if window else self.server.shutdown,daemon=True).start(); return
            return self.send_json({'error':'Ruta inexistente'},404)
        except ConnectionError:
            self.close_connection=True
        except PermissionError as exc: self.send_json({'error':str(exc)},403); self.discard_body()
        except Exception as exc:
            self.state.record_error(exc,'HTTP POST')
            logging.exception('POST failed'); self.send_json({'error':str(exc)},400); self.discard_body()

    def discard_body(self):
        """Drain an unread rejected body: closing with pending input makes Windows
        reset the connection, which can destroy the error response in transit."""
        self.close_connection=True
        try:
            self.connection.settimeout(0.2)
            while self.connection.recv(65536): pass
        except OSError: pass

def main(argv=None):
    if '--pick' in sys.argv:
        from .picker import main as pick
        sys.argv=[sys.argv[0],sys.argv[sys.argv.index('--pick')+1]]; pick(); return
    parser=argparse.ArgumentParser(description='Cursed Workshop — Gates of Hell')
    parser.add_argument('--port',type=int,default=0); parser.add_argument('--no-browser',action='store_true'); parser.add_argument('--browser',action='store_true',help='Open in the default browser instead of a window'); parser.add_argument('--workspace',type=Path,default=default_workspace())
    args=parser.parse_args(argv)
    args.workspace.mkdir(parents=True,exist_ok=True)
    handler=RotatingFileHandler(args.workspace/'workshop.log',maxBytes=4_000_000,backupCount=2,encoding='utf-8')
    logging.basicConfig(level=logging.INFO,handlers=[handler],format='%(asctime)s %(levelname)s %(message)s')
    logging.info('Cursed Workshop %s started (%s, Python %s)',__version__,platform.platform(),platform.python_version())
    state=AppState(args.workspace)
    try: state.closed()
    except Exception as exc:
        if not args.no_browser:
            import tkinter as tk
            from tkinter import messagebox
            window=tk.Tk(); window.withdraw(); messagebox.showerror('Cursed Workshop',str(exc)); window.destroy()
        else: print(str(exc),flush=True)
        return 2
    server=WorkshopServer(('127.0.0.1',args.port),Handler); server.state=state; server.window=None; server.last_seen=server.bye=None
    url=f'http://127.0.0.1:{server.server_port}/'; print('WORKSHOP_URL='+url,flush=True)
    try:
        if args.no_browser or args.browser or not run_window(server,url):
            if not args.no_browser:
                webbrowser.open(url,new=1)
                if running_under_wine(): show_address(url)
                close_with_page(server)
            server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt: pass
    finally:
        server.server_close(); state.pool.shutdown(wait=True)
        state.clear_portrait_previews()
        state.stop_catalog_warmup()
        if state.index: state.index.close()
    return 0

def page_gone(seen,bye,busy,now,*,idle=300,grace=15) -> bool:
    """Browser mode: the tool has no window of its own, so it ends a few seconds
    after its page closes (or after 5 minutes without hearing from it), never
    during an operation. Otherwise it keeps running unseen and Steam still shows
    it as playing, which blocks starting it again."""
    if seen is None or busy: return False
    if bye is not None and bye>=seen: return now-bye>grace
    return now-seen>idle

def close_with_page(server):
    def watch():
        last=time.monotonic()
        while True:
            time.sleep(2); now=time.monotonic()
            # A long gap means the PC slept: give the page time to call again.
            if now-last>30 and server.last_seen is not None: server.last_seen=now
            last=now
            with server.state.lock: busy=any(job['status'] in ('queued','running') for job in server.state.jobs.values())
            if page_gone(server.last_seen,server.bye,busy,now):
                logging.info('The page was closed; ending the browser session'); server.shutdown(); return
    threading.Thread(target=watch,daemon=True).start()

def show_address(url):
    """On Linux the host browser may not open by itself: say where the tool is."""
    def box():
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,f'Cursed Workshop is running.\n\nIf it did not open in your browser, open this address:\n{url}\n\n'
                                             'You can close this message: the tool keeps running until you press Exit in it or close its page.',
                                             'Cursed Workshop',0x40)
        except Exception:
            logging.exception('Could not show the address')
    threading.Thread(target=box,daemon=True).start()

def run_window(server,url) -> bool:
    """Show the workshop in its own desktop window (Edge WebView2).

    The local server runs on a background thread for the window's lifetime;
    closing the window ends the program. Returns False when no native window
    is available, so the caller falls back to the default browser.
    """
    if not getattr(sys,'frozen',False):
        deps=app_directory()/'.build-deps'
        if deps.is_dir() and str(deps) not in sys.path: sys.path.append(str(deps))
    elif os.name=='nt':
        # A download (GitHub, Nexus) extracted with Explorer marks every file as
        # coming from the internet; .NET then refuses Python.NET's and WebView2's
        # assemblies and the window falls back to the browser. Clear the mark
        # from the program's own libraries, as Unblock-File does.
        for library in app_directory().rglob('*.dll'):
            try:os.remove(str(library)+':Zone.Identifier')
            except OSError:pass
    try:
        import webview
        if os.name=='nt':
            # Without Edge WebView2 (Wine/Proton, very old Windows) pywebview
            # silently uses Internet Explorer's engine, which shows the page
            # but cannot run the app. The system browser can.
            from webview.platforms import winforms
            if winforms.renderer!='edgechromium':
                logging.warning('Edge WebView2 unavailable (renderer %s); using the browser',winforms.renderer); return False
    except Exception:
        logging.exception('Native window unavailable; using the browser'); return False
    thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.25},daemon=True)
    thread.start()
    try:
        server.window=webview.create_window('Cursed Workshop — Gates of Hell',url,width=1440,height=900,
                                            min_size=(1100,700),background_color='#0f171c',text_select=True,
                                            maximized=server.state.ui_preferences().get('maximized',True))
        def remember_window(maximized):
            try:server.state.ui_preferences({'maximized':maximized})
            except OSError:logging.exception('Could not save window preference')
        server.window.events.maximized+=lambda:remember_window(True)
        server.window.events.restored+=lambda:remember_window(False)
        webview.start(gui='edgechromium' if os.name=='nt' else None,debug=False)
    except Exception:
        logging.exception('Native window failed; using the browser')
        server.window=None
        if not thread.is_alive(): return False
        webbrowser.open(url,new=1); thread.join(); return True
    server.shutdown(); thread.join()
    return True
