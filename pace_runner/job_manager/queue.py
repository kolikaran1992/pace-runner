import json
from pathlib import Path
from typing import List, Optional
from dataclasses import asdict

from pace_runner.settings import config, logger

from pace_runner.job_manager.schema import SystemJobPayload, JobPayloadBase, JobStatus
from pace_runner.job_manager.lock_manager import job_file_lock


# --- STATIC CONFIGURATION PATHS ---
try:
    # We only need the one queue directory now
    JOBS_DIR = Path(config.output__job_queue)
except AttributeError:
    logger.exception(
        "Configuration Error: Job queue path not found. Check settings.toml for 'output__job_queue'."
    )
    # Re-raise to halt execution if core paths are missing
    raise

# Ensure directory exists
JOBS_DIR.mkdir(parents=True, exist_ok=True)
logger.info(f"Queue directory initialized: Pending/Failed='{JOBS_DIR}'")


# --- HELPER FUNCTIONS ---


def _get_job_path(job_id: str) -> Path:
    """Helper to construct the path for a job file in the single queue."""
    # All jobs (pending and failed) are stored in JOBS_DIR
    return JOBS_DIR / f"{job_id}.json"


def _get_job_by_id(job_id: str) -> SystemJobPayload:
    """
    Internal helper to retrieve and deserialize a single job file safely from JOBS_DIR.
    Raises FileNotFoundError if the file doesn't exist.
    """
    job_path = _get_job_path(job_id)

    try:
        # Acquire lock and receive the file handle (f) for atomic reading
        # We assume the lock is implemented to handle read/write access control
        with job_file_lock(job_id) as f:
            f.seek(0)
            job_data = json.load(f)
            # Deserialize the job data into a SystemJobPayload object
            return SystemJobPayload(**job_data)

    except FileNotFoundError:
        # Re-raise FileNotFoundError, but log a debug message
        logger.debug(f"Job file {job_id} not found at {job_path.as_posix()}.")
        raise
    except BlockingIOError as e:
        logger.warning(f"Job file {job_id} is locked, cannot retrieve: {e}")
        # Re-raise to let the caller know they couldn't access it
        raise
    except Exception as e:
        logger.exception(f"Failed to retrieve job {job_id} from queue: {e}")
        raise


def get_job_payload(job_id: str) -> SystemJobPayload:
    """
    Retrieves the SystemJobPayload object of a job by ID from the single queue.

    :param job_id: The ID of the job to retrieve.
    :raises FileNotFoundError: If the job is not found.
    :returns: The SystemJobPayload object.
    """
    # Only check the main queue (JOBS_DIR)
    try:
        return _get_job_by_id(job_id)
    except FileNotFoundError:
        # Not found anywhere
        raise FileNotFoundError(f"Job ID '{job_id}' not found in the queue.")

    # BlockingIOError and other exceptions are passed through from _get_job_by_id


def add_job(payload: JobPayloadBase, output_path: str) -> str:
    """
    Creates a new SystemJobPayload object from the payload and serializes it to disk.

    :param payload: The job-specific dataclass containing execution parameters.
    :param output_path: The file path where the API output should be saved.
    :return: The generated job_id string.
    """

    # 1. Create the SystemJobPayload with necessary system metadata (status defaults to 'pending')
    system_job = SystemJobPayload(payload=asdict(payload), output_path=output_path)

    job_path = _get_job_path(system_job.job_id)

    try:
        # Open in 'w' mode to create the file.
        with open(job_path, "w") as f:
            # Use asdict(system_job) to get a serializable dictionary
            json.dump(asdict(system_job), f, indent=4)

        logger.info(f"Job added: ID='{system_job.job_id}' at '{job_path}'")
        return system_job.job_id

    except Exception as e:
        logger.exception(f"Error saving job {system_job.job_id}: {e}")
        raise


def _get_jobs_in_queue(queue_dir: Path) -> List[Path]:
    """Helper to get a list of all job file paths in the specified directory."""
    return list(queue_dir.glob("*.json"))


def get_pending_jobs() -> List[SystemJobPayload]:
    """
    Retrieves all jobs in the queue that are NOT yet complete.
    This includes 'pending', 'processing', and 'failed' jobs.
    Locked files (being written by submitters or executors) are automatically skipped.
    """
    all_jobs: List[SystemJobPayload] = []
    # Statuses that we consider 'pending' for retrieval
    relevant_statuses = [JobStatus.pending, JobStatus.failed, JobStatus.processing]

    # Iterate over files in the jobs directory
    for file_path in _get_jobs_in_queue(JOBS_DIR):
        job_id = file_path.stem
        try:
            # Attempt to acquire a lock to ensure no other process is writing to it.
            with job_file_lock(job_id):
                # Load and deserialize the job data atomically
                job_data = json.load(file_path.open("r"))
                system_job = SystemJobPayload(**job_data)

                # Only include jobs that haven't been completed
                if system_job.status in relevant_statuses:
                    all_jobs.append(system_job)

        except FileNotFoundError:
            logger.error(f"job file not found at: {file_path}")
        except BlockingIOError:
            # Lock is currently held (e.g., by a job submission or a worker). Skip this job.
            logger.error(f"Skipping job {job_id}: file is currently locked.")
            continue
        except json.JSONDecodeError:
            logger.error(f"Error decoding job file {job_id}. Skipping.")
            continue
        except Exception as e:
            logger.error(f"Unexpected error processing job {job_id}: {e}")
            continue

    return all_jobs


def remove_all_jobs() -> int:
    """
    Deletes all job files from the single jobs/pending/failed queue.
    It attempts to acquire a lock before deleting each file to prevent conflicts.

    :return: The total count of job files successfully removed.
    """
    total_removed_count = 0
    queue_dir = JOBS_DIR  # Only one directory to clear

    logger.warning("ATTEMPTING TO REMOVE ALL JOBS from the single queue!")

    jobs_in_queue = _get_jobs_in_queue(queue_dir)
    queue_removed_count = 0

    for job_path in jobs_in_queue:
        job_id = job_path.stem
        try:
            # Atomically acquire the lock before deletion
            with job_file_lock(job_id):
                job_path.unlink()
                queue_removed_count += 1
                total_removed_count += 1

        except BlockingIOError:
            logger.warning(
                f"Skipping deletion of job {job_id}: file is currently locked by another process."
            )
        except FileNotFoundError:
            # Already deleted by another thread/process, treat as successful removal.
            queue_removed_count += 1
            total_removed_count += 1
        except Exception as e:
            logger.exception(f"Failed to remove job {job_id} from queue: {e}")

    logger.info(f"Removed {queue_removed_count} jobs from the queue.")
    logger.warning(f"Operation complete. Total jobs removed: {total_removed_count}.")
    return total_removed_count


def remove_job_by_id(job_id: str) -> bool:
    """
    Deletes a single job file from the queue by its ID.
    It attempts to acquire a lock before deleting the file.

    :param job_id: The ID of the job to remove.
    :return: True if the job was successfully removed (or already gone), False otherwise.
    """
    job_path = _get_job_path(job_id)

    logger.warning(f"ATTEMPTING TO REMOVE single job: {job_id}")

    try:
        # Atomically acquire the lock before deletion
        with job_file_lock(job_id):
            # Check if the file exists before attempting to unlink
            if job_path.exists():
                job_path.unlink()
                logger.info(f"Successfully removed job: {job_id}")
                return True
            else:
                # File not found but lock was acquired, treat as successful removal (already gone)
                logger.warning(
                    f"Job file {job_id} not found, but proceeding with removal (likely already gone)."
                )
                return True

    except BlockingIOError:
        logger.warning(
            f"Deletion failed: Job file {job_id} is currently locked by another process."
        )
        return False
    except FileNotFoundError:
        # File was deleted between path creation and lock acquisition, treat as success.
        logger.info(f"Job file {job_id} was already removed.")
        return True
    except Exception as e:
        logger.exception(f"Failed to remove job {job_id}: {e}")
        return False


def update_job_status(
    job_id: str, new_status: str, error_message: Optional[str] = None
) -> None:
    """
    Atomically updates the status and related metadata of a job file.
    """
    # Note: Because all jobs are in the single directory, we don't need to check
    # two directories to find the job.
    try:
        # Acquire lock and receive the file handle (f) for read/write
        # Assumes pending_job_file_lock opens the file in 'r+' mode
        with job_file_lock(job_id) as f:
            # 1. Rewind and Read
            f.seek(0)
            job_data = json.load(f)

            # 2. Modify Data
            job_data["status"] = new_status
            if error_message:
                # Ensure the job has an error_messages list initialized
                if "error_messages" not in job_data:
                    job_data["error_messages"] = []
                job_data["error_messages"].append(error_message)

            # Increment retries only if the job is failing or being marked for processing
            if new_status == "failed" or new_status == "processing":
                job_data["retries"] = job_data.get("retries", 0) + 1

            # 3. Truncate and Write (critical step for 'r+' mode)
            f.seek(0)
            f.truncate()
            json.dump(job_data, f, indent=4)

            logger.info(
                f"Status updated for job {job_id}: {new_status}. Retries: {job_data.get('retries', 0)}"
            )

    except FileNotFoundError:
        logger.warning(f"Update failed: Job file {job_id} not found.")
    except BlockingIOError:
        logger.warning(
            f"Update failed: Job file {job_id} is locked, cannot update status."
        )
    except Exception as e:
        logger.exception(f"Failed to update status for job {job_id}: {e}")
        raise


def mark_job_complete(job_id: str) -> None:
    """
    Atomically deletes the job file from the queue.
    The job is considered successfully processed and no longer needed.
    """
    job_path = _get_job_path(job_id)

    try:
        # Acquire the lock to ensure no parallel process is reading or modifying it before deletion.
        with job_file_lock(job_id):
            job_path.unlink()
            logger.info(f"Job {job_id} marked complete and deleted.")

    except FileNotFoundError:
        pass
    except BlockingIOError:
        logger.warning(f"Deletion failed: Job file {job_id} is locked, cannot delete.")
    except Exception as e:
        logger.exception(f"Failed to delete job {job_id}: {e}")
        raise


def move_to_failed(job_id: str, error_message: str) -> None:
    """
    Updates the job's status to 'failed' and records the error message.
    The job file remains in the single queue (JOBS_DIR).
    """
    try:
        # Simply update the status; no file movement is performed.
        update_job_status(job_id, JobStatus.failed, error_message)

        logger.warning(f"Job {job_id} status updated to failed. Error: {error_message}")

    except FileNotFoundError:
        logger.warning(f"Failure update failed: Job file {job_id} not found.")
    except Exception as e:
        logger.exception(f"Failed to update job {job_id} to failed status: {e}")
        raise
