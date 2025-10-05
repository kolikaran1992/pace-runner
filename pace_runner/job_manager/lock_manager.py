import fcntl
import time
from contextlib import contextmanager
from pace_runner.settings import config, logger
from pathlib import Path


# Ensure the base directory exists before any lock attempts
try:
    # Use Path for clean directory creation
    Path(config.output__base).mkdir(exist_ok=True, parents=True)
except AttributeError:
    # Handle cases where config might not be fully loaded or structured yet (e.g., mock/testing)
    logger.exception(
        "Warning: Could not access config.output__base. ensure that toml file contains output__base variable"
    )
except Exception as e:
    logger.exception(f"Error creating base directory {config.output__base}: {e}")


@contextmanager
def cron_process_lock():
    """
    Context manager for the CRON PROCESS LOCK.
    This lock prevents the cron scheduler from running two instances
    of the job_executor simultaneously. This is the 'global' lock.

    Configuration: Uses config.output__base (jobs directory) and
                   config.output__lock_timeout_sec (timeout).

    Usage:
    from lock_manager import cron_process_lock
    with cron_process_lock():
        # Code here runs exclusively
        ...
    """
    jobs_dir = config.output__base
    timeout = config.output__lock_timeout_sec
    # Use Path objects for cleaner path construction
    lock_file_path = Path(jobs_dir).joinpath(".cron_lock").as_posix()

    # 1. Open the file handle
    try:
        # Open in write mode, creating if it doesn't exist.
        f = open(lock_file_path, "w")
    except OSError as e:
        logger.exception(f"Error opening lock file {lock_file_path}: {e}")
        raise

    lock_acquired = False
    try:
        # 2. Try to acquire the lock (LOCK_EX = exclusive lock, LOCK_NB = non-blocking)
        # We use a non-blocking lock inside a loop to implement the timeout.
        start_time = time.time()

        while time.time() < start_time + timeout:
            try:
                # Attempt a non-blocking lock
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                lock_acquired = True
                break
            except BlockingIOError:
                # Lock is held, wait a short period and retry
                time.sleep(0.1)

        if not lock_acquired:
            raise TimeoutError(
                f"Could not acquire cron process lock within {timeout} seconds."
            )

        # Log successful acquisition
        logger.info("CRON LOCK: Acquired exclusive process lock.")

        # 3. Yield control to the 'with' block
        yield

    finally:
        # 4. Release the lock and close the file handle
        if lock_acquired:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            # Log successful release
            logger.info("CRON LOCK: Released exclusive process lock.")
        f.close()


@contextmanager
def job_file_lock(job_id: str):
    """
    Context manager for a JOB FILE LOCK.
    This lock ensures atomic read/write operations on a specific job file.

    Configuration: Uses config.output__base (jobs directory).

    Usage:
    from lock_manager import job_file_lock
    with job_file_lock('job_xyz') as f:
        # Safely read/write job_xyz.json using file handle 'f'
        ...
    """
    jobs_dir = config.output__job_queue
    job_file_path = Path(jobs_dir).joinpath(f"{job_id}.json").as_posix()

    # This lock needs to be acquired quickly or the operation fails,
    # as the cron lock handles overall scheduling.

    # We open outside the lock attempt but inside the context manager scope
    # to handle the FileNotFoundError gracefully.
    f = None
    try:
        # Note: Must exist for r+, but queue manager handles creation
        f = open(job_file_path, "r+")
    except FileNotFoundError:
        # This is expected if the file hasn't been written yet (e.g., in submit_job)
        # Or if it was just completed/deleted. We can't lock a non-existent file.
        raise FileNotFoundError(f"Cannot lock file: {job_file_path} not found.")

    try:
        # Try a non-blocking lock for immediate access
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        # Log successful acquisition
        logger.debug(f"JOB LOCK: Acquired lock for job ID: {job_id}")

        yield f  # Yield the file handle itself for atomic read/write

    except BlockingIOError:
        raise BlockingIOError(
            f"Job file lock for {job_id} is currently held by another process."
        )

    finally:
        # Only attempt to release/close if the file was successfully opened
        if f:
            # Release the lock and close the file handle
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            # Log successful release
            logger.debug(f"JOB LOCK: Released lock for job ID: {job_id}")
            f.close()
