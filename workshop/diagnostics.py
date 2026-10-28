"""Bounded, text-only support data. Never read saves, raw logs or arbitrary files."""
from collections import deque
from datetime import datetime, timezone
import platform
import math
import re
import traceback
import unicodedata

from . import __version__
from .build_info import read_build_info


def clean_text(value, *, secrets=(), limit=1200):
    text = str(value)[:16000]
    for secret in secrets:
        if secret:
            text = text.replace(str(secret), '[redacted]')
    text = re.sub(r'(?i)\b(?:https?|file)://[^\s<>\"]+', '[link removed]', text)
    text = re.sub(r'(?i)(?:[a-z]:[\\/]|\\\\)[^\r\n\"<>|]*', '[local path]', text)
    text = re.sub(r'(?<![\w/])/(?:home|Users|tmp|var|opt)/[^\s\"<>]*', '[local path]', text)
    text = re.sub(r'(?i)\b(token|password|secret|authorization|api[_-]?key)\s*[:=]\s*[^\s,;]+', r'\1=[redacted]', text)
    text = ''.join(c for c in text if c in '\n\t' or unicodedata.category(c)[0] != 'C')
    text = text.replace('`', "'").replace('@', '[at]')
    return text[:limit] + (' [truncated]' if len(text) > limit else '')


class Diagnostics:
    def __init__(self):
        self.errors = deque(maxlen=12)
        self.operations = deque(maxlen=12)
        self.phases = deque(maxlen=24)

    def record_operation(self, operation, status, elapsed_ms, queued_ms, secrets=()):
        self._record_timing(self.operations,operation,status,elapsed_ms,queued_ms,secrets)

    def record_phase(self, label, status, elapsed_ms, secrets=()):
        self._record_timing(self.phases,label,status,elapsed_ms,0,secrets)

    def _record_timing(self, history, operation, status, elapsed_ms, queued_ms, secrets):
        if status not in ('done', 'error', 'cancelled') or not all(isinstance(v, (int, float)) and math.isfinite(v) and v >= 0 for v in (elapsed_ms, queued_ms)):
            return
        history.append({'time_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                                'operation': clean_text(operation, secrets=secrets, limit=120),
                                'status': status, 'elapsed_ms': round(elapsed_ms), 'queued_ms': round(queued_ms)})

    def record(self, exc, operation, secrets=()):
        # Only frame identifiers, never source lines, locals or full filenames.
        frames = deque(maxlen=8)
        for frame, line in traceback.walk_tb(exc.__traceback__):
            module = frame.f_globals.get('__name__', 'unknown')
            frames.append(clean_text(f'{module}:{frame.f_code.co_name}:{line}', secrets=secrets, limit=160))
        self.errors.append({
            'time_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'operation': clean_text(operation, secrets=secrets, limit=120),
            'type': clean_text(type(exc).__name__, limit=80),
            'message': clean_text(exc, secrets=secrets), 'frames': list(frames),
        })

    def snapshot(self, index, campaign_loaded, secrets=()):
        sources = getattr(index, 'sources', ())
        mods = [{key: clean_text(source.get(key, ''), secrets=secrets, limit=100)
                 for key in ('id', 'version')} for source in sources[:60]]
        # Whitelist fields instead of exporting settings, a campaign or a job result.
        return {'schema': 1, 'version': __version__, 'build':read_build_info(), 'python': platform.python_version(),
                'platform': platform.system() + ' ' + platform.release(),
                'campaign_loaded': bool(campaign_loaded), 'resources_indexed': index is not None,
                'sources': mods, 'sources_omitted': max(0, len(sources) - len(mods)),
                'errors': list(self.errors), 'recent_operations': list(self.operations),
                'recent_phases':list(self.phases)}
