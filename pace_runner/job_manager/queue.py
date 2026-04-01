import json
from pathlib import Path
from typing import List, Optional, Dict, Any, Callable
from dataclasses import asdict

from pace_runner.settings import config, logger

# Assuming these schemas are available in the runtime environment
from pace_runner.job_manager.schema import SystemJobPayload, JobPayloadBase, JobStatus
from pace_runner.job_manager.lock_manager import _safe_open, _safe_remove_dir
from pace_runner.job_manager.payload_hash import (
    _calculate_payload_hash_key,
    _get_canonical_unique_string,
)


# --- STATIC CONFIGURATION PATHS ---
try:
    # JOBS_DIR is the BASE path for all hash folders
    JOBS_DIR = Path(config.output.job_queue)
except AttributeError:
    logger.exception(
        "Configuration Error: Job queue path not found. Check settings.toml for 'output__job_queue'."
    )
    # Re-raise to halt execution if core paths are missing
    raise

# Ensure directory exists
JOBS_DIR.mkdir(parents=True, exist_ok=True)
logger.info(f"Queue directory initialized: Base='{JOBS_DIR}'")

# Path to the index file mapping job_id to its full path string
JOB_ID_INDEX_FILE = JOBS_DIR / ".job_id_to_paths.json"


# --- CORE PATH & INDEX HELPERS ---


def _create_job_file_path(hash_key: str, job_id: str) -> Path:
    """
    Calculates and returns the full Path object for a job file based on its
    hash key and job ID. This combines hash folder path generation and file naming.
    """
    hash_folder = JOBS_DIR / hash_key
    return hash_folder / f"{job_id}.json"


def _read_job_index() -> Dict[str, str]:
    """Reads the job ID index atomically. Handles corruption/missing file."""
    try:
        with _safe_open(JOB_ID_INDEX_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        logger.error("Job ID index file corrupted. Starting with an empty index.")
        _write_job_index({})
        return {}


def _write_job_index(index: Dict[str, str]):
    """Writes the job ID index atomically."""
    try:
        with _safe_open(JOB_ID_INDEX_FILE, "w") as f:
            json.dump(index, f, indent=4)
    except Exception as e:
        logger.exception(f"Failed to write job ID index: {e}")


def _get_job_file_path(job_id: str) -> Path:
    """
    Retrieves a job's path from the index (renamed from _get_job_path_from_index).
    Raises FileNotFoundError if the job ID is not in the index.
    """
    path_str = _read_job_index().get(job_id)
    if not path_str:
        raise FileNotFoundError(f"Job ID '{job_id}' not found in the index.")
    return Path(path_str)


def _update_job_index(job_id: str, job_path: Optional[Path] = None) -> None:
    """Adds/updates a job's path in the index or removes it if job_path is None."""
    index = _read_job_index()
    if job_path:
        index[job_id] = job_path.as_posix()
    elif job_id in index:
        del index[job_id]
    _write_job_index(index)


def _get_jobs_in_queue() -> List[str]:
    """Returns list of job IDs currently in the queue index."""
    return list(_read_job_index().keys())


# --- CENTRALIZED JOB FILE I/O ---


def _load_job(job_id: str) -> SystemJobPayload:
    """
    Centralized loader: gets job path, acquires lock via safe_open, and loads/returns SystemJobPayload.
    """
    # Uses the renamed path retrieval function
    job_path = _get_job_file_path(job_id)
    with _safe_open(job_path, "r") as f:
        # Note: Safe open handles the file lock acquisition and release.
        return SystemJobPayload(**json.load(f))


def _save_job(system_job: SystemJobPayload, job_path: str) -> None:
    """
    Saves or updates a SystemJobPayload atomically and updates the index.
    """
    # Ensure hash folder exists
    job_path.parent.mkdir(parents=True, exist_ok=True)

    with _safe_open(job_path, "w") as f:
        json.dump(asdict(system_job), f, indent=4)

    # Update index
    _update_job_index(system_job.job_id, job_path)


def _update_job_file_atomically(
    job_id: str, updater: Callable[[Dict[str, Any]], None]
) -> None:
    """
    Atomically reads, modifies (using the updater callback), and writes a job file.
    """
    # Uses the renamed path retrieval function
    job_path = _get_job_file_path(job_id)
    with _safe_open(job_path, "r+") as f:
        job_data = json.load(f)
        updater(job_data)
        f.seek(0)
        f.truncate()
        json.dump(job_data, f, indent=4)


def _remove_job_file_and_folder(job_id: str) -> None:
    """
    Deletes a job file by ID and attempts to remove the parent folder (if empty).
    Does NOT modify the job index.
    """
    try:
        # Uses the renamed path retrieval function
        job_path = _get_job_file_path(job_id)
        if job_path.exists():
            job_path.unlink()
            try:
                # Attempt to remove the parent hash directory if it's now empty
                job_path.parent.rmdir()
            except OSError:
                # Directory wasn't empty
                pass
    except FileNotFoundError:
        pass  # File was already missing or not in index


def _update_job_status(
    job_id: str, new_status: str, error_message: Optional[str] = None
) -> None:
    """Updates a job's status, error messages, and retry count atomically."""

    def updater(data: Dict[str, Any]):
        data["status"] = new_status
        if error_message:
            data.setdefault("error_messages", []).append(error_message)
        # Assuming JobStatus is available and imported
        if new_status in {JobStatus.failed, JobStatus.processing}:
            data["retries"] = data.get("retries", 0) + 1

    _update_job_file_atomically(job_id, updater)


def _mark_job_complete(job_id: str) -> None:
    """
    Atomically deletes the job file from the queue and removes the index entry.
    """
    success = remove_job_by_id(job_id)
    if not success:
        logger.warning(
            f"Job {job_id} could not be marked complete (file may be locked)."
        )


def _move_to_failed(job_id: str, error_message: str) -> None:
    """
    Marks a job as failed and appends an error message.
    """
    try:
        _update_job_status(job_id, JobStatus.failed, error_message)
        logger.warning(f"Job {job_id} marked as failed. Error: {error_message}")
    except FileNotFoundError:
        logger.warning(f"Failed to mark job {job_id} as failed: file not found.")
    except Exception as e:
        logger.exception(f"Unexpected error marking job {job_id} as failed: {e}")
        raise


# --- QUEUE MANAGEMENT FUNCTIONS ---


def get_job_payload(job_id: str) -> SystemJobPayload:
    """
    Retrieves the SystemJobPayload object of a job by ID.
    This is the single function for retrieving full job data by ID (wrapper for _load_job).
    """
    try:
        return _load_job(job_id)
    except FileNotFoundError:
        # Re-raise the exception with a more descriptive message for the public API
        raise FileNotFoundError(f"Job ID '{job_id}' not found in the queue.")


def get_pending_jobs() -> List[SystemJobPayload]:
    """
    Retrieves all jobs that are pending, failed, or processing by iterating over all job IDs.
    """
    all_jobs: List[SystemJobPayload] = []
    # Assuming JobStatus is imported correctly
    relevant_statuses = {JobStatus.pending, JobStatus.failed, JobStatus.processing}

    # Use the reusable helper to get only the IDs
    for job_id in _get_jobs_in_queue():
        try:
            # Use the single function for loading job data by ID
            job = _load_job(job_id)
            if job.status in relevant_statuses:
                all_jobs.append(job)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.error(f"Skipping job {job_id}: file missing or corrupted.")
        except BlockingIOError:
            logger.error(f"Skipping job {job_id}: file is currently locked.")
        except Exception as e:
            logger.exception(f"Unexpected error processing job {job_id}: {e}")

    return all_jobs


def remove_job_by_id(job_id: str) -> bool:
    """
    Deletes a single job's file and removes its index entry.
    Returns True if successful or job was already missing.
    """
    try:
        # 1. Remove file and parent folder (helper uses lock)
        _remove_job_file_and_folder(job_id)

        # 2. Remove job entry from index
        _update_job_index(job_id)

        logger.debug(f"Job {job_id} successfully removed.")
        return True
    except BlockingIOError:
        logger.warning(f"Deletion failed: Job {job_id} is locked.")
        return False
    except Exception as e:
        logger.exception(f"Failed to remove job {job_id}: {e}")
        return False


def clean_job_queue() -> int:
    """
    Deletes everything in the job queue
    """
    _safe_remove_dir(JOBS_DIR)
    logger.warning(f"Cleared everything from the job queue")


def add_job(payload: JobPayloadBase, output_path: str) -> str:
    """
    Adds a new job to the queue, avoids duplicates, and updates the job index.
    Utilizes _save_job for atomic file writing and index update.

    :param payload: Job-specific dataclass (instance of JobPayloadBase).
    :param output_path: Path where API output should be saved.
    :return: The generated job_id string, or ID of an existing duplicate job.
    """
    new_job_hash_key = payload._hash_key
    new_job_pld_string = _get_canonical_unique_string(payload, payload)

    # Use _create_job_file_path to derive the hash folder path needed for the glob check.
    # We use a dummy ID and get the parent to avoid introducing a new helper.
    hash_folder = _create_job_file_path(new_job_hash_key, "dummy_id").parent

    # Ensure hash folder exists (done defensively here, but also handled by _save_job)
    hash_folder.mkdir(parents=True, exist_ok=True)

    # --- Duplicate check ---
    for job_file in hash_folder.glob("*.json"):
        try:
            # We must use safe_open for the read lock during the check
            with _safe_open(job_file, "r") as f:
                same_hash_system_job = SystemJobPayload(**json.load(f))
            # Compare payloads for duplicate
            # Must reconstruct the specific payload type for correct comparison
            same_hash_pld_unique_string = _get_canonical_unique_string(
                same_hash_system_job.payload, payload
            )
            if same_hash_pld_unique_string == new_job_pld_string:
                logger.info(
                    f"DUPLICATE JOB SKIPPED: Payload hash '{same_hash_pld_unique_string}' already present "
                    f"as job '{same_hash_system_job.job_id}'."
                )
                return same_hash_system_job.job_id
        except (FileNotFoundError, json.JSONDecodeError, BlockingIOError):
            # Skip corrupted or currently locked files
            continue

    # --- Create and save new job ---
    system_job = SystemJobPayload(payload=asdict(payload), output_path=output_path)
    # Use the new path creation helper to get the final path for logging
    job_path = _create_job_file_path(new_job_hash_key, system_job.job_id)

    try:
        # Use the reusable _save_job helper for atomic save and index update
        # _save_job internally uses _create_job_file_path and _update_job_index
        _save_job(system_job, job_path)
        logger.info(f"Job added: ID='{system_job.job_id}' at path '{job_path}'")
        return system_job.job_id
    except Exception as e:
        logger.exception(f"Error saving job {system_job.job_id}: {e}")
        # Clean up index entry if the save process failed
        _update_job_index(system_job.job_id)
        raise
