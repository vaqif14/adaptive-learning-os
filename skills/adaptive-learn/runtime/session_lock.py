"""Reentrant session locks shared by threads, processes and ledger writers.

The local runtime supports macOS/Linux. Never silently run unlocked on a
platform without flock. Lock files are stable and must not be unlinked.
"""
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
import os
import threading
import weakref

_guard = threading.Lock()
_locks = weakref.WeakValueDictionary()
_held = threading.local()


@contextmanager
def session_lock(directory: Path):
    import fcntl
    directory = Path(directory).resolve()
    key = str(directory)
    with _guard:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        held = getattr(_held, "paths", None)
        if held is None:
            held = _held.paths = set()
        if key in held:
            yield
            return
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(directory / ".session.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            held.add(key)
            try:
                yield
            finally:
                held.remove(key)
        finally:
            os.close(fd)


def locked_session(method):
    @wraps(method)
    def locked(self, session_id, *args, **kwargs):
        with session_lock(self.dir(session_id)):
            self._recover_state(session_id)
            return method(self, session_id, *args, **kwargs)
    return locked
