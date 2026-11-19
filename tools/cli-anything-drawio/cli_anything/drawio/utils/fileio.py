"""Locked file writes (see cli-anything-plugin/guides/session-locking.md).

Never use bare ``open("w")`` for shared state: it truncates before any lock
can be taken. Open ``"r+"``, lock, then truncate inside the lock.
"""

from __future__ import annotations

import json
import os


def _lock(f):
    """Take an exclusive lock; returns an unlock callable (or None)."""
    try:
        import fcntl

        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        return lambda: fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except ImportError:
        pass
    except OSError:
        return None
    try:
        import msvcrt

        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)

        def _unlock():
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)

        return _unlock
    except (ImportError, OSError):
        return None  # unsupported filesystem — proceed unlocked


def _open_for_locked_write(path, encoding):
    try:
        return open(path, "r+", encoding=encoding, newline="")
    except FileNotFoundError:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        return open(path, "w", encoding=encoding, newline="")


def locked_write_text(path, text, encoding="utf-8"):
    """Write ``text`` to ``path`` under an exclusive lock."""
    with _open_for_locked_write(path, encoding) as f:
        unlock = _lock(f)
        try:
            f.seek(0)
            f.truncate()
            f.write(text)
            f.flush()
        finally:
            if unlock is not None:
                try:
                    unlock()
                except OSError:
                    pass


def locked_save_json(path, data, **dump_kwargs):
    """Write JSON to ``path`` under an exclusive lock."""
    locked_write_text(path, json.dumps(data, **dump_kwargs))
