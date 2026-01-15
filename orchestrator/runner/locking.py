"""
Lock management for AOS.

Uses flock for global locking and per-workstream locking.
Includes defunct process detection and stale lock recovery.
"""

import fcntl
import json
import logging
import os
import shutil
import signal
import socket
import sys
import time
import atexit
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager
from typing import Optional

from orchestrator.notifications import notify

logger = logging.getLogger(__name__)


class LockTimeout(Exception):
    """Lock acquisition timed out."""
    pass


CONCURRENCY_WARNING_THRESHOLD = 3
LOCK_RECOVERY_ENABLED = True


def _get_boot_id() -> str:
    """Get current system boot ID (changes on reboot)."""
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except (IOError, OSError):
        return "unknown"


def _read_lock_info(lock_file: Path) -> Optional[dict]:
    """
    Read lock file info, supporting both JSON and legacy PID-only format.

    Returns dict with lock info, or None if file doesn't exist or is unreadable.
    """
    if not lock_file.exists():
        return None

    try:
        content = lock_file.read_text().strip()
        if not content:
            return None

        # Try JSON format first
        if content.startswith('{'):
            return json.loads(content)

        # Legacy PID-only format
        try:
            pid = int(content.split('\n')[0])
            return {
                'pid': pid,
                'version': 0,  # Legacy marker
            }
        except ValueError:
            return None
    except (IOError, OSError, json.JSONDecodeError) as e:
        logger.debug(f"Failed to read lock file {lock_file}: {e}")
        return None


def _is_holder_defunct(lock_info: dict) -> bool:
    """
    Check if the process holding the lock is defunct (dead/crashed).

    Returns True if holder is definitely dead/invalid.
    Returns False if holder appears to be alive (conservative).
    """
    pid = lock_info.get('pid')
    if not pid:
        return True

    proc_dir = Path(f"/proc/{pid}")

    # Check 1: PID existence
    if not proc_dir.exists():
        logger.debug(f"Lock holder PID {pid} does not exist")
        return True

    # Check 2: Boot ID (reboot detection)
    lock_boot_id = lock_info.get('boot_id')
    if lock_boot_id and lock_boot_id != "unknown":
        current_boot_id = _get_boot_id()
        if current_boot_id != "unknown" and lock_boot_id != current_boot_id:
            logger.info(f"Lock holder PID {pid} from different boot session")
            return True

    # Check 3: Zombie state
    try:
        stat_content = (proc_dir / "stat").read_text()
        # Format: pid (comm) state ...
        # State is after the closing paren
        if ')' in stat_content:
            after_paren = stat_content.split(')')[1].strip()
            if after_paren and after_paren[0] == 'Z':
                logger.info(f"Lock holder PID {pid} is zombie")
                return True
    except (IOError, OSError, IndexError):
        pass  # Can't determine, assume alive

    # Check 4: Process identity (is it still our process?)
    # Only check for our specific signatures - don't accept generic 'python'
    # as that would defeat PID reuse detection
    try:
        cmdline = (proc_dir / "cmdline").read_text()
        if 'orchestrator' not in cmdline and '/wf' not in cmdline:
            logger.info(f"Lock holder PID {pid} is different process type")
            return True
    except (IOError, OSError):
        pass  # Can't read, assume alive

    return False


def _recover_stale_lock(lock_file: Path, stale_info: dict) -> bool:
    """
    Attempt to recover a stale lock. Race-safe via recovery marker file.

    Returns True if recovery succeeded and caller should retry acquisition.
    Returns False if recovery failed (another process is recovering).
    """
    recovery_file = lock_file.with_suffix('.recovering')

    # Step 1: Atomic claim of recovery slot
    try:
        fd = os.open(str(recovery_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"{os.getpid()}".encode())
        os.close(fd)
    except FileExistsError:
        logger.debug("Another process is already recovering this lock")
        return False
    except OSError as e:
        logger.warning(f"Failed to create recovery marker: {e}")
        return False

    try:
        # Step 2: Re-verify lock is still stale (double-check)
        current_info = _read_lock_info(lock_file)
        if current_info != stale_info:
            logger.debug("Lock was updated during recovery attempt")
            return False

        # Step 3: Archive stale lock for debugging
        stale_pid = stale_info.get('pid', 'unknown')
        ws_id = stale_info.get('workstream_id', lock_file.stem)
        try:
            stale_archive = lock_file.with_suffix(f'.stale.{int(time.time())}')
            shutil.copy(lock_file, stale_archive)
            logger.info(f"Archived stale lock to {stale_archive}")
        except (IOError, OSError) as e:
            logger.debug(f"Could not archive stale lock: {e}")

        # Step 4: Remove the stale lock file
        try:
            lock_file.unlink()
            logger.warning(f"Recovered stale lock for {ws_id} (was PID {stale_pid})")

            # Send notification
            try:
                notify(
                    f"Hashd: Lock Recovered",
                    f"Recovered stale lock for {ws_id} (was PID {stale_pid})",
                    "normal"
                )
            except Exception as e:
                logger.debug(f"Failed to send lock recovery notification: {e}")

            return True
        except FileNotFoundError:
            return True  # Already gone
        except OSError as e:
            logger.warning(f"Failed to remove stale lock: {e}")
            return False
    finally:
        # Step 5: Clean up recovery marker
        try:
            recovery_file.unlink()
        except (FileNotFoundError, OSError):
            pass


def _is_lock_held(lock_file: Path) -> bool:
    """
    Check if a lock file is currently held by a valid (non-defunct) process.

    Returns True if lock is held by a live process.
    Returns False if lock is free, held by defunct process, or on error.
    """
    if not lock_file.exists():
        return False

    # First check if holder is defunct
    lock_info = _read_lock_info(lock_file)
    if lock_info and _is_holder_defunct(lock_info):
        # Holder is defunct - try to recover if enabled
        if LOCK_RECOVERY_ENABLED:
            if _recover_stale_lock(lock_file, lock_info):
                return False  # Lock was recovered, now free
        # If recovery disabled or failed, still report as held
        # (will timeout eventually)

    # Check via flock
    try:
        fd = open(lock_file, 'r')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False  # Got lock = no one else has it
        except BlockingIOError:
            return True  # Could not get lock = someone has it
        finally:
            fd.close()
    except (IOError, OSError) as e:
        logger.debug(f"Failed to check lock {lock_file}: {e}")
        return False


def is_workstream_locked(ops_dir: Path, workstream_id: str) -> bool:
    """Check if a workstream lock is currently held by a running process."""
    lock_file = ops_dir / "locks" / "workstreams" / f"{workstream_id}.lock"
    return _is_lock_held(lock_file)


def get_lock_info(ops_dir: Path, workstream_id: str) -> Optional[dict]:
    """
    Get lock info for a workstream if it's locked by a valid process.

    Returns lock info dict if locked, None otherwise.
    """
    lock_file = ops_dir / "locks" / "workstreams" / f"{workstream_id}.lock"
    if not lock_file.exists():
        return None

    lock_info = _read_lock_info(lock_file)
    if lock_info and not _is_holder_defunct(lock_info):
        # Verify via flock that it's actually held
        try:
            fd = open(lock_file, 'r')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
                return None  # Got lock = no one else has it
            except BlockingIOError:
                return lock_info  # Locked and valid
            finally:
                fd.close()
        except (IOError, OSError):
            return None

    return None


def count_running_workstreams(ops_dir: Path) -> int:
    """Count how many workstreams are currently locked (running)."""
    lock_dir = ops_dir / "locks" / "workstreams"
    if not lock_dir.exists():
        return 0

    return sum(1 for lock_file in lock_dir.glob("*.lock") if _is_lock_held(lock_file))


def cleanup_stale_lock_files(ops_dir: Path):
    """
    Clean up stale lock files from defunct processes.

    This is called at startup to recover from any crashed processes.
    """
    lock_dir = ops_dir / "locks" / "workstreams"
    if not lock_dir.exists():
        return

    for lock_file in lock_dir.glob("*.lock"):
        lock_info = _read_lock_info(lock_file)
        if lock_info and _is_holder_defunct(lock_info):
            if LOCK_RECOVERY_ENABLED:
                _recover_stale_lock(lock_file, lock_info)


# Global state for updating lock file during run.
# WARNING: Not thread-safe. This module assumes single-threaded CLI usage.
# Do not use in threaded/async contexts without adding proper synchronization.
_current_lock_file: Optional[Path] = None
_current_lock_fd = None


def update_lock_stage(stage: str):
    """
    Update the current stage in the lock file (for UI display).

    Note: This is best-effort and can race with other readers. The stage field
    is advisory only - lock validity is determined by flock, not file contents.
    """
    global _current_lock_file, _current_lock_fd
    if _current_lock_file is None or _current_lock_fd is None:
        return

    try:
        lock_info = _read_lock_info(_current_lock_file)
        if lock_info:
            lock_info['stage'] = stage
            # Write to temp file and rename for atomicity
            tmp_file = _current_lock_file.with_suffix('.lock.tmp')
            tmp_file.write_text(json.dumps(lock_info, indent=2))
            tmp_file.rename(_current_lock_file)
    except (IOError, OSError) as e:
        logger.debug(f"Failed to update lock stage: {e}")


@contextmanager
def _acquire_lock(lock_file: Path, timeout: int, lock_name: str,
                  workstream_id: str = None, run_id: str = None):
    """
    Internal helper to acquire a file lock with JSON metadata.

    Args:
        lock_file: Path to the lock file
        timeout: Seconds to wait for lock
        lock_name: Human-readable name for error messages
        workstream_id: Optional workstream ID for lock metadata
        run_id: Optional run ID for lock metadata
    """
    global _current_lock_file, _current_lock_fd

    lock_file.parent.mkdir(parents=True, exist_ok=True)

    start = time.time()
    fd = None

    while True:
        # Check for stale lock before trying to acquire
        if lock_file.exists() and LOCK_RECOVERY_ENABLED:
            lock_info = _read_lock_info(lock_file)
            if lock_info and _is_holder_defunct(lock_info):
                logger.info(f"Detected defunct lock holder, attempting recovery")
                if _recover_stale_lock(lock_file, lock_info):
                    logger.info("Stale lock recovered successfully")

        fd = open(lock_file, 'w')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            fd.close()
            fd = None
            if time.time() - start > timeout:
                raise LockTimeout(f"Could not acquire {lock_name} within {timeout}s")
            time.sleep(1)

    # Write JSON lock info
    lock_info = {
        'version': 1,
        'pid': os.getpid(),
        'hostname': socket.gethostname(),
        'start_time': datetime.now().isoformat(),
        'boot_id': _get_boot_id(),
        'stage': 'starting',
    }
    if workstream_id:
        lock_info['workstream_id'] = workstream_id
    if run_id:
        lock_info['run_id'] = run_id

    fd.write(json.dumps(lock_info, indent=2))
    fd.flush()

    # Set global state for stage updates
    _current_lock_file = lock_file
    _current_lock_fd = fd

    # Register cleanup
    def cleanup():
        global _current_lock_file, _current_lock_fd
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()
        except Exception as e:
            logger.debug(f"Lock cleanup failed (may be expected): {e}")
        _current_lock_file = None
        _current_lock_fd = None

    atexit.register(cleanup)
    original_sigterm = signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))
    original_sigint = signal.signal(signal.SIGINT, lambda *_: sys.exit(1))

    try:
        yield
    finally:
        atexit.unregister(cleanup)
        signal.signal(signal.SIGTERM, original_sigterm)
        signal.signal(signal.SIGINT, original_sigint)
        cleanup()


@contextmanager
def workstream_lock(ops_dir: Path, workstream_id: str, timeout: int = 60,
                    run_id: str = None):
    """
    Acquire per-workstream lock, yield, release on exit.

    Allows multiple workstreams to run in parallel.
    """
    lock_file = ops_dir / "locks" / "workstreams" / f"{workstream_id}.lock"
    with _acquire_lock(lock_file, timeout, f"lock for {workstream_id}",
                       workstream_id=workstream_id, run_id=run_id):
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
