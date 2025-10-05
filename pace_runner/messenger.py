import datetime
from typing import Optional
from abc import abstractmethod

from pace_runner.settings import logger, config


class MessengerBase:
    """
    Base messenger class defining the interface for sending notifications.

    The base class handles all message formatting and includes key system info
    like the log file path. Derived classes MUST override the _send_notification
    method to implement the actual message delivery.
    """

    def __init__(self):
        """Initializes the messenger and fetches the constant log path."""
        self.log_file_path = config.get("logs__file", "N/A (Check config)")
        logger.info(f"Base Messenger initialized. Log path: {self.log_file_path}")

    def _build_structured_message(
        self, title: str, status: str, details: str, job_id: Optional[str] = None
    ) -> str:
        """
        Helper to structure the output message string in a readable format.
        """
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")

        message = f"--- PaceRunner Notification ---\n"
        message += f"[{timestamp}] STATUS: {status}\n"
        message += f"TITLE: {title}\n"
        if job_id:
            message += f"JOB ID: {job_id}\n"

        message += f"LOG FILE: {self.log_file_path}\n"
        message += f"DETAILS:\n{details}\n"
        message += "------------------------------"
        return f"\n{message}\n"

    @abstractmethod
    def _send_notification(self, message: str) -> None:
        """
        [ABSTRACT METHOD]
        This method MUST be overridden by concrete implementations.
        It handles the actual API or transport layer for sending the message string.

        The base implementation logs and prints the message string directly.
        """
        pass

    def send_summary(
        self, processed_count: int, completed_count: int, failed_count: int
    ) -> None:
        """
        Formats and sends a summary report after a cron execution cycle completes.
        """
        details = (
            f"Total Processed: {processed_count}\n"
            f"Successfully Completed: {completed_count}\n"
            f"Failed (Moved to Failed Queue): {failed_count}"
        )

        message = self._build_structured_message(
            title="PaceRunner Execution Summary",
            status="CRON RUN COMPLETE",
            details=details,
        )

        self._send_notification(message)

    def send_alert(
        self,
        title: str,
        job_id: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """
        Formats and sends an immediate alert for critical failures.
        """
        details = (
            f"Error: {error_message}"
            if error_message
            else "No detailed error provided."
        )

        message = self._build_structured_message(
            title=title, status="CRITICAL ALERT", details=details, job_id=job_id
        )

        self._send_notification(message)
