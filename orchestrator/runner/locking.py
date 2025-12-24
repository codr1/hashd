"""
Lock management for AOS.

Uses flock for global locking to prevent concurrent runs.
"""

import fcntl
import os
import sys
import time
import signal
import atexit
from pathlib import Path
from contextlib import contextmanager


class LockTimeout(Exception):
    """Lock acquisition timed out."""
    pass


@contextmanager
def global_lock(ops_dir: Path, timeout: int = 600):
    """
    Acquire global lock, yield, release on exit.

    Ensures only one wf run executes at a time.
    Handles SIGTERM/SIGINT for graceful cleanup.
    """
    lock_file = ops_dir / "locks" / "global.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    fd = open(lock_file, 'w')
    start = time.time()

    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.time() - start > timeout:
                fd.close()
                raise LockTimeout(f"Could not acquire lock within {timeout}s")
            time.sleep(1)

    # Register cleanup
    def cleanup():
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()
        except Exception:
            pass

    atexit.register(cleanup)
    original_sigterm = signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))
    original_sigint = signal.signal(signal.SIGINT, lambda *_: sys.exit(1))

    try:
        fd.write(f"{os.getpid()}\n")
        fd.flush()
        yield
    finally:
        atexit.unregister(cleanup)
        signal.signal(signal.SIGTERM, original_sigterm)
        signal.signal(signal.SIGINT, original_sigint)
        cleanup()
