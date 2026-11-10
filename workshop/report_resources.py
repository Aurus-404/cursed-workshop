"""Bounded text-only snapshots of the resources a diagnostic needs.

Read through the existing resource index, including PAK members and mod overrides.
Never copy a whole archive, mesh, texture, executable or arbitrary filesystem path.
"""
import hashlib
import json
import zlib
from collections import deque
from pathlib import PurePosixPath

from .syntax import parse

MAX_FILES = 256
MAX_FILE_BYTES = 1024 * 1024
MAX_RAW_BYTES = 12 * 1024 * 1024
MAX_COMPRESSED_BYTES = 2 * 1024 * 1024
TEXT_SUFFIXES = {'.set', '.inc', '.ext', '.def', '.mdl', '.reg', '.pattern', '.presets'}
ROOTS = ('set/multiplayer/units/roster_conquest.set',
         'set/interaction_entity/entity.set', 'set/tp_control.set', 'set/registry/unit.reg')


def selection_from_diagnostic(text):
    """The existing diagnostic contains a JSON editor selection, not paths to open."""
    marker='Editor selection:'
    if marker not in text:return {}
    try:
        value,_=json.JSONDecoder().raw_decode(text.split(marker,1)[1].lstrip())
        return value if isinstance(value,dict) else {}
    except (ValueError,TypeError,RecursionError):return {}


def _templates(value,depth=0):
    if depth>16:return []
    found=[]
    if isinstance(value,dict):
        for key,item in value.items():
            if key in ('hull','module','recipient','donor','donor_template','template') and isinstance(item,str):
                if item and len(item)<=160:found.append(item)
            elif isinstance(item,(dict,list)):found.extend(_templates(item,depth+1))
    elif isinstance(value,list):
        for item in value:found.extend(_templates(item,depth+1))
    return list(dict.fromkeys(found))


def add_resource_snapshot(resources, selection, add, clean, room):
    """add(name,text) writes to the report; room excludes a manifest/ZIP reserve."""
    manifest={'schema':1,'templates':_templates(selection),'files':[],'errors':[],
              'limits':{'files':MAX_FILES,'file_bytes':MAX_FILE_BYTES,'raw_bytes':MAX_RAW_BYTES,
                        'compressed_bytes':min(MAX_COMPRESSED_BYTES,max(0,room))}}
    def fail(message):
        if len(manifest['errors'])<128:manifest['errors'].append(str(message)[:2000])
        else:manifest['omitted_errors']=manifest.get('omitted_errors',0)+1
    if resources is None:
        manifest['errors'].append('No resource index was available.')
        add('resources/manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2));return
    queue=deque();seen=set();raw_total=compressed_total=0
    try:
        registration=resources.registration_sources() if hasattr(resources,'registration_sources') else resources
    except Exception as error:
        fail('Registration view: '+str(error));registration=resources
    manifest['sources']=[{k:s[k] for k in ('id','name','root','version','priority','fingerprint') if k in s}
                         for s in getattr(resources,'sources',[])]
    # Prioritize the failing units' roster files even in very large mod stacks.
    # This uses the same effective include graph as the registration compiler.
    try:
        from .registration import _load_include_graph
        records,_=_load_include_graph(registration,ROOTS[0])
        wanted={n.casefold() for n in manifest['templates']}
        priority=[p for p,n in records if n.head.casefold() in wanted]
        priority.extend(p for p,n in records if n.head.casefold()=='define')
        for path in dict.fromkeys(priority):queue.append(('registration',registration,path))
    except Exception as error:fail('Roster graph: '+str(error))
    for path in ROOTS:queue.append(('registration',registration,path))
    for template in manifest['templates']:
        try:
            info=resources.resolve_template(template)
            for key in ('definition_path','model_path','extension_path'):
                if info.get(key):queue.append(('selection',resources,info[key]))
            for path in info.get('extension_paths',[]):queue.append(('selection',resources,path))
        except Exception as error:fail('Template '+template+': '+str(error))
    while queue and len(manifest['files'])<MAX_FILES:
        scope,view,path=queue.popleft()
        try:
            entry=view.resolve(path);path=entry.path
            key=(scope,path.casefold())
            if key in seen:continue
            seen.add(key)
            virtual=PurePosixPath(path)
            if virtual.name.casefold()!='.presets' and virtual.suffix.casefold() not in TEXT_SUFFIXES:continue
            row={'scope':scope,'path':path,'source':entry.as_dict(),'status':'error'}
            shadowed=[e.as_dict() for e in getattr(view,'collisions',{}).get(path.casefold(),[]) if e!=entry]
            if shadowed:row['shadowed_sources']=shadowed
            manifest['files'].append(row)
            if entry.size>MAX_FILE_BYTES:
                row.update(status='omitted',reason='file_size_limit');continue
            if raw_total+entry.size>MAX_RAW_BYTES:
                row.update(status='omitted',reason='raw_size_limit');continue
            raw=view.read_bytes(path,max_bytes=MAX_FILE_BYTES)
            raw_total+=len(raw);text=view.read_text(path)
            row['source_sha256']=hashlib.sha256(raw).hexdigest()
            exported=clean(text).encode('utf8')
            # Include both local and central ZIP headers in the budget estimate.
            name='resources/'+scope+'/'+path
            cost=len(zlib.compress(exported))+76+2*len(name.encode('utf8'))
            if compressed_total+cost>manifest['limits']['compressed_bytes']:
                row.update(status='omitted',reason='compressed_size_limit');continue
            add(name,text);compressed_total+=cost
            row.update(status='included',archive_path=name,export_sha256=hashlib.sha256(exported).hexdigest(),
                       exported_bytes=len(exported),redacted=exported!=text.encode('utf8'))
            try:
                doc=parse(text,lenient=True)
                for node in doc.walk('include'):
                    if node.args:
                        try:queue.append((scope,view,view.resolve_reference(node.args[0],path)))
                        except Exception as error:fail('Include '+path+' -> '+node.args[0]+': '+str(error))
            except ValueError as error:fail('Parse '+path+': '+str(error))
            # Some mods export macros through implicit folder presets.
            for parent in PurePosixPath(path).parents:
                if str(parent)=='.':break
                preset=str(parent/'.presets')
                if view.exists(preset):queue.append((scope,view,preset))
        except Exception as error:
            fail(scope+': '+str(path)+': '+str(error))
    if queue:fail('File limit reached; remaining resource dependencies were omitted.')
    manifest.update(read_bytes=raw_total,estimated_compressed_bytes=compressed_total)
    add('resources/manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
