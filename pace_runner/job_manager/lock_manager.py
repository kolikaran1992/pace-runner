import time, shutil, fcntl, os
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


def _safe_remove_dir(dir_path: str) -> bool:
    """
    Attempts to remove a directory and all its contents, acquiring a non-blocking lock
    on a lock file inside the directory to prevent race conditions.

    :param dir_path: Path to the directory to remove.
    :return: True if the directory was successfully removed, False if locked.
    """
    dir_path = Path(dir_path)
    if not dir_path.exists() or not dir_path.is_dir():
        return True  # Already gone, treat as success

    lock_file = dir_path / ".lock"  # hidden lock file
    lock_file.touch(exist_ok=True)

    try:
        with open(lock_file, "r+") as f:
            # Attempt non-blocking exclusive lock
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            # Lock acquired, safe to remove directory recursively
            shutil.rmtree(dir_path)
            return True

    except BlockingIOError:
        # Lock is held by another process
        return False
    except Exception as e:
        # Log or re-raise depending on your needs
        raise RuntimeError(f"Failed to remove directory {dir_path}: {e}")


@contextmanager
def _cron_process_lock():
    cron_file_path = Path(config.output.base).joinpath(".cron_lock").as_posix()
    with _safe_open(
        cron_file_path,
        "w",
        timeout=config.output.lock_timeout_sec,
        create_dirs=True,
    ) as f:
        # If we reached here, cron lock acquired
        yield f


@contextmanager
def _safe_open(
    file,
    mode="r",
    buffering=-1,
    encoding=None,
    errors=None,
    newline=None,
    closefd=True,
    opener=None,
    *,
    blocking=False,
    timeout=0,
    create_dirs=False,
):
    """
    Universal file opener with built-in file locking.

    Works exactly like built-in `open()`, but adds:
      - automatic flock-based locking
      - optional timeout or non-blocking mode
      - optional directory creation
      - raises FileNotFoundError if file does not exist (for read modes)

    Args:
        file (str or Path): Path to the file.
        mode (str): Same as `open()`.
        blocking (bool): If False, raises immediately if file locked.
        timeout (int): Max seconds to wait for lock if blocking=True.
        create_dirs (bool): If True, auto-creates parent directories.

    Usage:
        >>> with safe_open("data.txt", "a+") as f:
        ...     f.write("Hello, world")
    """
    path = Path(file)
    if create_dirs:
        path.parent.mkdir(parents=True, exist_ok=True)

    # --- Validate file existence if read mode ---
    if not path.is_file() and not any(m in mode for m in ("w", "a", "x")):
        raise FileNotFoundError(f"Cannot open '{path}': file does not exist.")

    # --- Open file ---
    try:
        f = open(path, mode, buffering, encoding, errors, newline, closefd, opener)
    except FileNotFoundError:
        # Catch any remaining edge cases (e.g., race conditions)
        raise FileNotFoundError(f"Cannot open '{path}': file not found.")

    start_time = time.time()
    locked = False

    try:
        # Exclusive lock for write modes, shared for read-only
        lock_type = (
            fcntl.LOCK_EX if any(m in mode for m in ("w", "a", "+")) else fcntl.LOCK_SH
        )

        while True:
            try:
                flags = lock_type
                if not blocking:
                    flags |= fcntl.LOCK_NB
                fcntl.flock(f.fileno(), flags)
                locked = True
                logger.debug(
                    f"LOCK: Acquired {'exclusive' if lock_type == fcntl.LOCK_EX else 'shared'} lock on {path}"
                )
                break
            except BlockingIOError:
                if not blocking:
                    raise BlockingIOError(f"File is locked: {path}")
                if time.time() - start_time > timeout:
                    raise TimeoutError(f"Timeout waiting for lock: {path}")
                time.sleep(0.1)

        yield f  # Give back the open file handle safely

    finally:
        if locked:
            try:
                f.flush()
                os.fsync(f.fileno())
            except Exception:
                pass  # flush may fail in read mode — ignore
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            logger.debug(f"LOCK: Released lock on {path}")
        f.close()
