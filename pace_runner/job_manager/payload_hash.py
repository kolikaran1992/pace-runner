from pace_runner.settings import config


import hashlib
import json
from dataclasses import fields
from typing import Any, Dict, List


# --- HASHING CONFIGURATION ---
MAX_HASH_INPUT_LENGTH = config.output.hash_input_length
MAX_NUMBER_OF_DIVISIONS = config.output.paylod_string_division
# ---

# ==============================================================================
# 1. FIELD INTROSPECTION HELPER
# ==============================================================================


def _get_comparable_field_names(obj: Any) -> List[str]:
    """
    Returns a list of field names from a dataclass instance or class type that are
    marked for comparison (i.e., f.compare is True).
    """
    if not hasattr(obj, "__dataclass_fields__"):
        raise TypeError(f"Object {obj} is not a dataclass instance or class type.")

    comparable_fields = [f.name for f in fields(obj) if f.compare]
    return comparable_fields


# ==============================================================================
# 2. UNIQUE STRING GENERATOR (Updated Function)
# ==============================================================================


def _get_canonical_unique_string(data: Any, metadata_source: Any) -> str:
    """
    Generates a deterministic JSON string representation based on the data provided,
    filtering fields using the comparison rules defined in the metadata_source.

    Args:
        data: The payload data (e.g., a dataclass instance, a dictionary, or nested list/dict).
        metadata_source: The dataclass instance or class type used to inspect
                         which fields are marked for comparison (compare=True).

    Returns:
        A canonical JSON string.
    """
    if not hasattr(metadata_source, "__dataclass_fields__"):
        # If the metadata source is not a dataclass (e.g., standard list/dict in recursion),
        # we treat the data as a plain object and serialize everything within it.
        return json.dumps(data, sort_keys=True, default=str)

    unique_data: Dict[str, Any] = {}

    # Use the metadata_source (the dataclass instance/type) to determine which fields to include
    comparable_names = _get_comparable_field_names(metadata_source)

    for f_name in comparable_names:
        # 1. Get the value from the data source (which could be a dict or a dataclass instance)
        if isinstance(data, dict):
            value = data.get(f_name)
        else:
            # Assumes data is a dataclass instance or has the attribute
            value = getattr(data, f_name)

        # 2. Get the field info to determine the type of nested object
        field_info = next(
            (f for f in fields(metadata_source) if f.name == f_name), None
        )

        # 3. Check if the value itself is a nested dataclass type
        if field_info and hasattr(field_info.type, "__dataclass_fields__"):
            # If the value is a nested dataclass, recursively call the function,
            # using the nested dataclass type as the new metadata_source.
            unique_data[f_name] = json.loads(
                _get_canonical_unique_string(value, field_info.type)
            )
        else:
            # If it's a primitive or non-dataclass object (like a List[str])
            unique_data[f_name] = value

    # Create the final canonical string. The keys MUST be sorted for consistency.
    return json.dumps(unique_data, sort_keys=True, default=str)


# ==============================================================================
# 3. HASH KEY CALCULATOR (Externalized Logic)
# ==============================================================================


def _calculate_payload_hash_key(payload_instance: Any) -> str:
    """
    Calculates the Bounded Hash Key (SHA256 digest) for a JobPayload instance.
    This implements the Bounded Sampling Strategy on the unique string.
    """
    # Use the instance as both the data source (for values) and the metadata source (for rules)
    unique_payload_string = _get_canonical_unique_string(
        payload_instance, payload_instance
    )

    L = len(unique_payload_string)
    N = MAX_HASH_INPUT_LENGTH
    M = MAX_NUMBER_OF_DIVISIONS

    # Determine the size of the sampled segment
    segment_size_sampled = N // M

    # Determine the full block size in the original string (the distance between samples)
    block_size_full = L // M

    sampled_string_parts = []

    for i in range(M):
        start_index = i * block_size_full
        end_index = start_index + segment_size_sampled

        if start_index >= L:
            break

        sampled_string_parts.append(unique_payload_string[start_index:end_index])

    # Finalize the bounded hash input string, including the length L
    final_hash_input = "".join(sampled_string_parts) + f"|L={L}|"

    # Calculate the final SHA256 digest
    return hashlib.sha256(final_hash_input.encode("utf-8")).hexdigest()
