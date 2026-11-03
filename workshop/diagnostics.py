"""Bounded, text-only support data. Never read saves, raw logs or arbitrary files."""
from collections import deque
from datetime import datetime, timezone
import platform
import math
import logging
import os
import threading
from contextvars import ContextVar
import re
import traceback
import unicodedata

from . import __version__
from .build_info import read_build_info

timing_job = ContextVar('timing_job', default='background')


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
    def __init__(self, timing_path=None):
        self.timing_path = timing_path
        self.timing_write_failed = False
        self._write_lock = threading.Lock()
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
        row = {'time_utc': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
                                'operation': clean_text(operation, secrets=secrets, limit=120),
                                'status': status, 'elapsed_ms': round(elapsed_ms), 'queued_ms': round(queued_ms)}
        history.append(row)  # one C call: safe beside a snapshot's list() of it
        if self.timing_path is not None:
            kind = 'operation' if history is self.operations else 'phase'
            flag = ' SLOW' if elapsed_ms + queued_ms >= 1000 else ''
            line = f"{row['time_utc']} | {timing_job.get()} | {kind}{flag} | {row['operation']} | {status} | elapsed_ms={row['elapsed_ms']} | queued_ms={row['queued_ms']}\n"
            try:
                with self._write_lock:
                    # Two files of at most TIMING_LIMIT: a report stays small
                    # however long the tool has been used.
                    if self.timing_path.is_file() and self.timing_path.stat().st_size > self.TIMING_LIMIT:
                        os.replace(self.timing_path, self._previous_timings())
                    with self.timing_path.open('a', encoding='utf-8') as stream:
                        stream.write(line)
            except OSError:
                if not self.timing_write_failed:
                    logging.exception('Load timing history unavailable')
                self.timing_write_failed = True

    TIMING_LIMIT = 512 * 1024

    def _previous_timings(self):
        return self.timing_path.with_name(self.timing_path.name + '.1')

    def timing_report(self):
        lines = ['Load timing history (UTC). SLOW = execution + queue >= 1000 ms.',
                 'Match phases to operations by job ID. Nested phase durations overlap.']
        if self.timing_path is not None:
            with self._write_lock:
                for path in (self._previous_timings(), self.timing_path):
                    try:
                        lines.append(path.read_text(encoding='utf-8').rstrip('\n'))
                    except FileNotFoundError:
                        if path == self.timing_path and len(lines) == 2:
                            lines.append('No recorded loads.')
                    except OSError:
                        lines.append('Timing history could not be read.')
        if self.timing_write_failed:
            lines.append('Some timings could not be saved.')
        return '\n'.join(lines)

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
