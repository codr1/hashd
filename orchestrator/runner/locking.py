"""
Lock management for AOS.

Uses flock for global locking and per-workstream locking.
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


CONCURRENCY_WARNING_THRESHOLD = 3


def count_running_workstreams(ops_dir: Path) -> int:
    """Count how many workstreams are currently locked (running)."""
    lock_dir = ops_dir / "locks" / "workstreams"
    if not lock_dir.exists():
        return 0

    count = 0
    for lock_file in lock_dir.glob("*.lock"):
        try:
            fd = open(lock_file, 'r')
            try:
                # Try non-blocking exclusive lock
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                # Got lock - means no one else has it, release immediately
                fcntl.flock(fd, fcntl.LOCK_UN)
            except BlockingIOError:
                # Could not get lock - workstream is running
                count += 1
            finally:
                fd.close()
        except (IOError, OSError):
            pass
    return count


def cleanup_stale_lock_files(ops_dir: Path):
    """
    Placeholder for future lock file cleanup.

    Note: We intentionally do NOT delete lock files. Deleting creates a race
    condition where two processes can end up with "exclusive" locks on different
    inodes with the same path. Lock files are tiny (~10 bytes) so accumulation
    is not a real problem.
    """
    pass


@contextmanager
def _acquire_lock(lock_file: Path, timeout: int, lock_name: str):
    """
    Internal helper to acquire a file lock.

    Args:
        lock_file: Path to the lock file
        timeout: Seconds to wait for lock
        lock_name: Human-readable name for error messages
    """
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
                raise LockTimeout(f"Could not acquire {lock_name} within {timeout}s")
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


@contextmanager
def workstream_lock(ops_dir: Path, workstream_id: str, timeout: int = 60):
    """
    Acquire per-workstream lock, yield, release on exit.

    Allows multiple workstreams to run in parallel.
    """
    lock_file = ops_dir / "locks" / "workstreams" / f"{workstream_id}.lock"
    with _acquire_lock(lock_file, timeout, f"lock for {workstream_id}"):
        yield


@contextmanager
def global_lock(ops_dir: Path, timeout: int = 600):
    """
    Acquire global lock, yield, release on exit.

    Used for merge operations that touch the main branch.
    """
    lock_file = ops_dir / "locks" / "global.lock"
    with _acquire_lock(lock_file, timeout, "global lock"):
        yield
