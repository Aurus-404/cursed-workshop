"""Cooperative cancellation for read-only jobs; never interrupt publication."""
from contextvars import ContextVar


class JobCancelled(Exception):
    pass


current_cancel = ContextVar('workshop_cancel', default=None)
current_progress = ContextVar('workshop_progress', default=None)


def checkpoint():
    event = current_cancel.get()
    if event is not None and event.is_set():
        raise JobCancelled('Operation cancelled by user')


def report_progress(completed, total):
    callback=current_progress.get()
    if callback is not None:callback(completed,total)
