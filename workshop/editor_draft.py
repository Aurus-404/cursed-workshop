"""Durable editor-only recovery; never changes a campaign or publishes assets."""
import json
import os
from pathlib import Path
from datetime import datetime, timezone

LIMIT=900_000

def valid_options(value):
    if not isinstance(value,dict) or set(value)-{'autosave','snap','move_step','angle_step','fine_factor','bindings'}:return False
    for key,item in value.items():
        if key in ('autosave','snap'):
            if type(item) is not bool:return False
        elif key=='bindings':
            allowed={'move','rotate','space','frame','hide','isolate','show','lock','undo','redo','save','cancel','search','help','snap_hold','fine_hold'}
            if not isinstance(item,dict) or set(item)-allowed or any(not isinstance(v,str) or len(v)>70 for v in item.values()):return False
        elif isinstance(item,bool) or not isinstance(item,(int,float)) or not 0.001<=item<=({'fine_factor':1,'angle_step':360}.get(key,10000)):return False
    return True

def access(workspace,body=None):
    path=Path(workspace)/'editor-draft.json'
    if body is None:
        try:return json.loads(path.read_text('utf-8'))
        except FileNotFoundError:return {'draft':None}
    if not isinstance(body,dict) or set(body)!={'draft'}:raise ValueError('Invalid editor draft.')
    draft=body['draft']
    if draft is not None and (not isinstance(draft,dict) or draft.get('schema')!=1 or not isinstance(draft.get('transform'),dict) or not isinstance(draft.get('hull'),dict)):
        raise ValueError('Invalid editor draft.')
    raw=json.dumps({'draft':draft,'saved_utc':datetime.now(timezone.utc).isoformat()},ensure_ascii=False,allow_nan=False)
    if len(raw.encode('utf-8'))>LIMIT:raise ValueError('The editor draft is too large.')
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    try:
        with temp.open('w',encoding='utf-8') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:
        if temp.exists():temp.unlink()
    return {'saved':True}
