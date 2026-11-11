"""Process and system memory for the log, never for decisions.

Under Proton a process killed by Linux (out of memory) leaves no trace in the
game's or Wine's logs; these figures in workshop.log are what show it coming.
GlobalMemoryStatusEx reports the host's memory under Wine as well.
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


class _Counters(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD), ('PeakWorkingSetSize', ctypes.c_size_t),
                ('WorkingSetSize', ctypes.c_size_t), ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPagedPoolUsage', ctypes.c_size_t), ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                ('QuotaNonPagedPoolUsage', ctypes.c_size_t), ('PagefileUsage', ctypes.c_size_t),
                ('PeakPagefileUsage', ctypes.c_size_t)]


class _Status(ctypes.Structure):
    _fields_ = [('dwLength', wintypes.DWORD), ('dwMemoryLoad', wintypes.DWORD), ('ullTotalPhys', ctypes.c_uint64),
                ('ullAvailPhys', ctypes.c_uint64), ('ullTotalPageFile', ctypes.c_uint64), ('ullAvailPageFile', ctypes.c_uint64),
                ('ullTotalVirtual', ctypes.c_uint64), ('ullAvailVirtual', ctypes.c_uint64), ('ullAvailExtendedVirtual', ctypes.c_uint64)]


def snapshot() -> dict:
    """{'rss', 'peak', 'available', 'total'} in MB; missing figures are left out."""
    result = {}
    if os.name != 'nt':
        return result
    try:
        counters = _Counters(); counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL('kernel32'); psapi = ctypes.WinDLL('psapi')
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD]
        if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            result.update(rss=counters.WorkingSetSize >> 20, peak=counters.PeakWorkingSetSize >> 20)
        status = _Status(); status.dwLength = ctypes.sizeof(status)
        if kernel.GlobalMemoryStatusEx(ctypes.byref(status)):
            result.update(available=status.ullAvailPhys >> 20, total=status.ullTotalPhys >> 20)
    except (OSError, AttributeError):
        pass
    return result


def describe() -> str:
    figures = snapshot()
    if not figures:
        return 'memory unknown'
    parts = []
    if 'rss' in figures:
        parts.append(f"rss={figures['rss']}MB peak={figures['peak']}MB")
    if 'available' in figures:
        parts.append(f"free={figures['available']}/{figures['total']}MB")
    return ' '.join(parts)
