"""Fixed packaged metadata, with no repository, network or user-file discovery."""
import hashlib
import json
import re
import sys
from pathlib import Path


def source_code_sha256(folder):
    """Catalog semantics depend on backend code, independently of docs/UI."""
    digest = hashlib.sha256()
    for path in sorted(Path(folder).glob('*.py')):
        digest.update(path.name.encode() + b'\0' + path.read_bytes().replace(b'\r\n', b'\n'))
    return digest.hexdigest()


def read_build_info():
    fallback={'kind':'unavailable' if getattr(sys,'frozen',False) else 'source checkout'}
    path=Path(__file__).with_name('build_info.json')
    try:
        if path.stat().st_size>4096:return fallback
        raw=json.loads(path.read_text(encoding='utf8'))
        if not isinstance(raw,dict) or not re.fullmatch('[0-9a-f]{64}',raw.get('source_sha256','')):return fallback
        result={'kind':'packaged build','source_sha256':raw['source_sha256']}
        catalog = raw.get('catalog_code_sha256')
        if isinstance(catalog,str) and re.fullmatch('[0-9a-f]{64}',catalog):result['catalog_code_sha256']=catalog
        if isinstance(raw.get('revision'),str) and re.fullmatch('[0-9a-f]{40}',raw['revision']):result['revision']=raw['revision']
        if isinstance(raw.get('built_utc'),str) and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00',raw['built_utc']):result['built_utc']=raw['built_utc']
        if type(raw.get('dirty_source')) is bool:result['dirty_source']=raw['dirty_source']
        return result
    except (OSError,ValueError,TypeError):return fallback
