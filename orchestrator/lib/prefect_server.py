"""Prefect server management.

Auto-starts Prefect server if not running. Used by commands that need
flow orchestration (wf run, wf watch).
"""

import logging
import os
import subprocess
import time
from urllib.error import URLError
from urllib.request import urlopen

logger = logging.getLogger(__name__)

PREFECT_SERVER_URL = "http://127.0.0.1:4200"
PREFECT_API_URL = f"{PREFECT_SERVER_URL}/api"
HEALTH_ENDPOINT = f"{PREFECT_API_URL}/health"

# How long to wait for server to start
SERVER_START_TIMEOUT_SECONDS = 30
HEALTH_CHECK_INTERVAL_SECONDS = 0.5


def is_server_running() -> bool:
    """Check if Prefect server is running and healthy."""
    try:
        with urlopen(HEALTH_ENDPOINT, timeout=2) as response:
            return response.status == 200
    except (URLError, TimeoutError, OSError):
        return False


def start_server() -> subprocess.Popen:
    """Start Prefect server in background.

    Returns the Popen object for the server process.
    """
    logger.info("Starting Prefect server...")

    # Start server with output redirected
    process = subprocess.Popen(
        ["prefect", "server", "start", "--host", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        # Don't let server die when parent exits
        start_new_session=True,
    )

    return process


def wait_for_server(timeout: float = SERVER_START_TIMEOUT_SECONDS) -> bool:
    """Wait for Prefect server to become healthy.

    Returns True if server is healthy, False if timeout.
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        if is_server_running():
            return True
        time.sleep(HEALTH_CHECK_INTERVAL_SECONDS)
    return False


def ensure_prefect_server() -> None:
    """Ensure Prefect server is running, starting it if necessary.

    Also sets PREFECT_API_URL environment variable so flows connect to the server.

    Raises:
        RuntimeError: If server fails to start within timeout.
    """
    # Set API URL so Prefect client knows where to connect
    os.environ["PREFECT_API_URL"] = PREFECT_API_URL

    if is_server_running():
        logger.debug("Prefect server already running")
        return

    # Start server
    start_server()

    # Wait for it to be ready
    if not wait_for_server():
        raise RuntimeError(
            f"Prefect server failed to start within {SERVER_START_TIMEOUT_SECONDS} seconds. "
            f"Try starting manually: prefect server start"
        )

    logger.info(f"Prefect server started at {PREFECT_SERVER_URL}")


def get_prefect_dashboard_url() -> str:
    """Get URL for Prefect dashboard."""
    return PREFECT_SERVER_URL


# Worker management
WORKER_POOL_NAME = "hashd-workers"


def is_worker_running() -> bool:
    """Check if a worker is running for our pool.

    Checks by querying the Prefect API for active workers.
    """
    if not is_server_running():
        return False

    try:
        # Query workers via API
        import json
        from urllib.request import Request, urlopen

        # List work pools
        req = Request(
            f"{PREFECT_API_URL}/work_pools/filter",
            data=json.dumps({}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urlopen(req, timeout=2) as response:
            pools = json.loads(response.read())
            # Check if our pool exists and has workers
            for pool in pools:
                if pool.get("name") == WORKER_POOL_NAME:
                    # Pool exists - check if it has active workers
                    # Workers are considered active if they've polled recently
                    return pool.get("status") == "READY"
        return False
    except Exception:
        return False


def ensure_work_pool() -> None:
    """Create the work pool if it doesn't exist."""
    import json
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError

    try:
        # Try to create the pool
        req = Request(
            f"{PREFECT_API_URL}/work_pools/",
            data=json.dumps({
                "name": WORKER_POOL_NAME,
                "type": "process",
                "description": "Worker pool for hashd workstream execution"
            }).encode(),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urlopen(req, timeout=5) as response:
            logger.info(f"Created work pool: {WORKER_POOL_NAME}")
    except HTTPError as e:
        if e.code == 409:  # Conflict - already exists
            pass
        else:
            logger.warning(f"Failed to create work pool: {e}")


def start_worker() -> subprocess.Popen:
    """Start a Prefect worker in background."""
    logger.info(f"Starting Prefect worker for pool: {WORKER_POOL_NAME}")

    process = subprocess.Popen(
        ["prefect", "worker", "start", "--pool", WORKER_POOL_NAME],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return process


def wait_for_worker(timeout: float = SERVER_START_TIMEOUT_SECONDS) -> bool:
    """Wait for worker to become ready."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        if is_worker_running():
            return True
        time.sleep(HEALTH_CHECK_INTERVAL_SECONDS)
    return False


def ensure_prefect_worker() -> None:
    """Ensure a Prefect worker is running, starting one if necessary.

    Requires server to be running first.

    Raises:
        RuntimeError: If worker fails to start within timeout.
    """
    if not is_server_running():
        raise RuntimeError("Prefect server must be running before starting worker")

    # Ensure the work pool exists
    ensure_work_pool()

    if is_worker_running():
        logger.debug("Prefect worker already running")
        return

    # Start worker
    start_worker()

    # Wait for it to be ready
    if not wait_for_worker():
        raise RuntimeError(
            f"Prefect worker failed to start within {SERVER_START_TIMEOUT_SECONDS} seconds. "
            f"Try starting manually: prefect worker start --pool {WORKER_POOL_NAME}"
        )

    logger.info(f"Prefect worker started for pool: {WORKER_POOL_NAME}")


def ensure_prefect_infrastructure() -> None:
    """Ensure both Prefect server and worker are running.

    Convenience function that calls both ensure_prefect_server()
    and ensure_prefect_worker().
    """
    ensure_prefect_server()
    ensure_prefect_worker()
