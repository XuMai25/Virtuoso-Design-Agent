"""Validated one-level OA instance paths shared across contracts and workers."""

from __future__ import annotations

import re


INSTANCE_SEGMENT = r"[A-Za-z_][A-Za-z0-9_$]*"
INSTANCE_PATH_PATTERN = rf"^(?:[^/\r\n]+|{INSTANCE_SEGMENT}/{INSTANCE_SEGMENT})$"


def split_instance_path(value: str) -> tuple[str | None, str]:
    """Return ``(top_instance, local_instance)`` for a one-level path."""

    if re.fullmatch(INSTANCE_PATH_PATTERN, value) is None:
        raise ValueError(
            "instance must be a nonempty top-level OA name or one-level "
            "TOP/CHILD identifier path"
        )
    parts = value.split("/")
    if len(parts) == 1:
        return None, parts[0]
    return parts[0], parts[1]


def is_scoped_instance_path(value: str) -> bool:
    top_instance, _local_instance = split_instance_path(value)
    return top_instance is not None


__all__ = [
    "INSTANCE_PATH_PATTERN",
    "is_scoped_instance_path",
    "split_instance_path",
]
