from dataclasses import dataclass, field
from abc import ABC
from uuid import uuid4
from datetime import datetime


from pace_runner.settings import config, logger


import hashlib
import json
from dataclasses import dataclass, field, fields, asdict
from typing import Any, List, Dict

# --- HASHING CONFIGURATION ---
# N: The maximum length of the string input used for the final hash calculation.
MAX_HASH_INPUT_LENGTH = config.output.hash_input_length  # n = 100
# M: The number of equally spaced segments to sample from the full unique payload string.
MAX_NUMBER_OF_DIVISIONS = config.output.paylod_string_division  # m = 4
# ---


def _get_canonical_unique_string(obj: Any) -> str:
    """
    Generates a deterministic string representation of the unique keys in a dataclass.

    This function iterates through the dataclass fields and includes only those
    that are marked for comparison (by default, if eq=True or compare=True).
    The dictionary is converted to a JSON string with sorted keys to ensure
    a canonical, deterministic order, regardless of object insertion order.

    NOTE: It does not sort the child lists, so two plds with exactly same elements
    in some child lists could result in different strings
    """
    if not hasattr(obj, "__dataclass_fields__"):
        # For nested objects that are not dataclasses (e.g., standard dict/list),
        # use JSON dump on the whole object for canonical representation.
        return json.dumps(obj, sort_keys=True, default=str)

    unique_data = {}

    # Iterate through the fields of the dataclass
    for f in fields(obj):
        # A field is considered part of the uniqueness definition if:
        # 1. It is used for equality checking (f.compare is True)
        # 2. It is not a field that holds large, irrelevant data.
        if f.compare:
            # Recursively handle nested dataclass objects
            value = getattr(obj, f.name)
            if hasattr(value, "__dataclass_fields__"):
                unique_data[f.name] = json.loads(_get_canonical_unique_string(value))
            else:
                unique_data[f.name] = value

    # Create the final canonical string. The keys MUST be sorted for consistency.
    return json.dumps(unique_data, sort_keys=True, default=str)


@dataclass(eq=False)
class JobPayloadBase:
    """
    The main job dataclass with custom bounded hashing logic.
    We set eq=False to manually control the hashing and equality check.

    Abstract base class for all API-specific job parameters.
    User-defined payload dataclasses must inherit from this.

    """

    _hash_key: str = field(init=False, repr=False, compare=False, default="")
    _unique_payload_string: str = field(
        init=False, repr=False, compare=False, default=""
    )

    def __init__(self, *args, **kwargs):
        """
        Custom __init__ to capture and discard non-init arguments.

        *args captures any excess positional arguments (and is ignored).
        **kwargs captures keyword arguments (which are then filtered).
        """

        # 1. Collect all init fields from this class and its bases (including subclasses)
        init_fields = {f.name for f in self.__dataclass_fields__.values() if f.init}

        # 2. Filter kwargs to keep only those corresponding to init fields
        filtered_kwargs = {k: v for k, v in kwargs.items() if k in init_fields}
        print(filtered_kwargs)
        # 3. Initialize fields by manually setting attributes for the filtered kwargs.
        super().__init__(self, *args, **filtered_kwargs)

    def __post_init__(self):
        """Calculate and cache the bounded hash input string upon object creation."""

        # 1. Get the full canonical string representation of ONLY the unique keys (O(K))
        self._unique_payload_string = _get_canonical_unique_string(self)

        # 2. Apply Bounded Sampling Strategy
        L = len(self._unique_payload_string)
        N = MAX_HASH_INPUT_LENGTH
        M = MAX_NUMBER_OF_DIVISIONS

        # Determine the size of the sampled segment
        segment_size_sampled = N // M

        # Determine the full block size in the original string (the distance between samples)
        # We ensure at least one full block is L/M, with steps adjusted for safety.
        block_size_full = L // M

        sampled_string_parts = []

        for i in range(M):
            # Calculate the starting point for the current block
            start_index = i * block_size_full

            # Calculate the end point for the sampled segment
            end_index = start_index + segment_size_sampled

            # Ensure boundaries are safe
            if start_index >= L:
                break  # We've exhausted the string

            # Append the sampled segment (safe slice)
            sampled_string_parts.append(
                self._unique_payload_string[start_index:end_index]
            )

        # 3. Finalize the bounded hash input string
        # Crucially, we include the original length L to differentiate between
        # a short payload and a long payload with identical samples.
        final_hash_input = "".join(sampled_string_parts) + f"|L={L}|"

        # 4. Calculate the final SHA256 digest
        self._hash_key = hashlib.sha256(final_hash_input.encode("utf-8")).hexdigest()

    def __hash__(self) -> int:
        """Returns the integer hash based on the pre-calculated digest."""
        # Use Python's built-in hash() on the small, fixed-length digest string
        return hash(self._hash_key)

    def __eq__(self, other) -> bool:
        """Equality check relies on the hash digest."""
        if not isinstance(other, JobPayloadBase):
            return NotImplemented
        return self._unique_payload_string == other._unique_payload_string

    def get_dict(self) -> dict:
        job_dict = asdict(self)
        # to avoid storing large strings copy of the payload itself in the dict
        del job_dict["_unique_payload_string"]
        return job_dict


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
