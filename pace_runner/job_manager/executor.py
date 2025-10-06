import json
from pathlib import Path
from typing import Dict, Any, List
from abc import abstractmethod

from pace_runner.settings import logger
from pace_runner.job_manager.lock_manager import _cron_process_lock
from pace_runner.job_manager.queue import (
    get_pending_jobs,
    _update_job_status,
    _mark_job_complete,
    _move_to_failed,
)
from pace_runner.job_manager.schema import JobStatus, SystemJobPayload
from pace_runner.messenger import MessengerBase

import traceback as tb


class JobExecutorBase:
    """
    The central worker component of PaceRunner. It is responsible for locking the process,
    retrieving pending jobs, dispatching them to the correct API handlers, handling
    failures/retries, and sending status notifications.
    """

    def __init__(self, messenger: MessengerBase):
        """
        Initializes the executor and accepts the messenger via Dependency Injection.

        Args:
            messenger: An object that adheres to the BaseMessenger interface
                       for sending notifications and alerts.
        """
        self.messenger: MessengerBase = messenger
        logger.info("JobExecutor initialized with provided messenger.")

    @abstractmethod
    def _execute_handler_logic(self, job: SystemJobPayload) -> Dict[str, Any]:
        """
        Abstract method defining the contract for executing a job's payload
        via an API-specific handler.
        Args:
            job: The SystemJobPayload object to be processed.

        Returns:
            A dictionary containing the API response/output data.
        """
        # A concrete implementation idea for the subclass:
        raise NotImplementedError("Subclasses must implement _execute_handler_logic()")

    def _save_output(self, job: SystemJobPayload, result: Dict[str, Any]) -> None:
        """
        Saves the execution result dictionary to the specified job.output_path.

        Args:
            job: The SystemJobPayload object containing the output_path.
            result: The dictionary of data to be saved (the API response).

        Raises:
            IOError: If there is an issue writing the file.
        """
        output_path = Path(job.output_path)

        # Ensure the parent directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(output_path, "w") as f:
                json.dump(result, f, indent=4)
            logger.debug(
                f"Successfully saved output for Job ID {job.job_id} to {output_path}"
            )
        except Exception as e:
            logger.error(f"Failed to save output for Job ID {job.job_id}: {e}")
            raise IOError(f"Error saving output to {output_path}: {e}")

    # --- 5. Main Execution Entry Point ---
    def run_cron_job(self) -> None:
        """
        The main entry point for the cron scheduler. Executes all pending jobs
        in a locked process, handles execution, saving, cleanup, and reporting.
        """
        processed_jobs: int = 0
        completed_jobs: int = 0
        failed_jobs: int = 0

        # 1. Global Lock: Ensure single-instance execution
        with _cron_process_lock():
            logger.info("Cron lock acquired. Starting job execution cycle.")

            # 2. Job Retrieval: Get all runnable jobs
            jobs_to_process: List[SystemJobPayload] = get_pending_jobs()
            logger.info(f"Found {len(jobs_to_process)} jobs to process.")

            # 3. Loop & Execute
            for job in jobs_to_process:
                processed_jobs += 1
                try:
                    logger.debug(f"Processing Job ID: {job.job_id}")

                    # Mark job as processing
                    _update_job_status(job.job_id, JobStatus.processing)

                    # Call the abstract execution logic
                    result: Dict[str, Any] = self._execute_handler_logic(job)

                    # Save the result
                    self._save_output(job, result)

                    # Mark job as complete and delete the entry/file
                    _mark_job_complete(job.job_id)
                    completed_jobs += 1

                    logger.info(f"Job ID {job.job_id} successfully completed.")

                # Error Handling
                except Exception as e:
                    failed_jobs += 1
                    logger.error(
                        f"FATAL ERROR while executing Job ID {job.job_id}: {e}",
                        exc_info=True,
                    )

                    # Send an immediate Alert
                    alert_message = (
                        f"🚨 PaceRunner Job Execution Failed! 🚨\n"
                        f"Error: `{type(e).__name__}: {str(e)}`"
                    )
                    self.messenger.send_alert(
                        "Job Execution Failure", job.job_id, alert_message
                    )

                    # Move the job to the failed queue
                    _move_to_failed(job.job_id, tb.format_exc())

            logger.info(
                f"Execution cycle finished. Processed: {processed_jobs}, Completed: {completed_jobs}, Failed: {failed_jobs}."
            )

        # Reporting: Send final counts summary outside the lock block (optional, but safe)
        self.messenger.send_summary(
            processed_count=processed_jobs,
            completed_count=completed_jobs,
            failed_count=failed_jobs,
        )
        logger.info("Summary report sent. Cron process exiting.")
