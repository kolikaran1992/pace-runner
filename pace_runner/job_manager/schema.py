from dataclasses import dataclass, field
from abc import ABC
from uuid import uuid4
from datetime import datetime


from pace_runner.settings import config, logger
from pace_runner.job_manager.payload_hash import _calculate_payload_hash_key

import hashlib
import json
from dataclasses import dataclass, field, fields, asdict
from typing import Any, List, Dict


@dataclass
class JobPayloadBase:
    """
    Abstract base class for all API-specific job parameters.
    User-defined payload dataclasses must inherit from this.
    """

    _hash_key: str = field(init=False, compare=False)

    def __post_init__(self) -> None:
        self._hash_key = _calculate_payload_hash_key(self)


@dataclass(frozen=True)
class JobStatus:
    pending: str = "pending"
    processing: str = "processing"
    complete: str = "complete"
    failed: str = "failed"


@dataclass
class SystemJobPayload:
    """
    The main job dataclass used internally by PaceRunner.
    It tracks the job's state regardless of the target API.
    """

    payload: dict = field(init=True)
    output_path: str = field(init=True)

    # System Metadata
    job_id: str = field(
        default_factory=lambda: f"{config.output__job_name}__{str(uuid4())}", init=True
    )
    created_at: datetime = field(default_factory=lambda: config.now_iso, init=True)

    # Execution Tracking
    status: str = field(default=JobStatus.pending)
    retries: int = field(default=0)
    error_messages: str = field(default_factory=list)
