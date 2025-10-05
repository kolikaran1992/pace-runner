"""
A simple, file-system-backed library to handle rate-limited job execution via cron.

This module provides the main entry points for configuration, job management,
and execution.
"""

from .settings import config
from .job_manager import (
    JobPayloadBase,
    SystemJobPayload,
    JobExecutorBase,
    add_job,
    get_job_payload,
    get_pending_jobs,
    remove_all_jobs,
    remove_job_by_id,
)
from .messenger import MessengerBase


__all__ = [
    # Configuration
    "config",
    # Core Job Management Functions/Classes
    "add_job",
    "JobPayloadBase",
    "SystemJobPayload",
    "get_job_payload",
    "get_pending_jobs",
    "remove_all_jobs",
    "remove_job_by_id",
    # Executor and Messenger
    "JobExecutorBase",
    "MessengerBase",
]
