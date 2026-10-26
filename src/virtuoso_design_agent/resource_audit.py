"""Read-only inventory for transient resources and retained EDA evidence."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any


_TRANSIENT_PATTERNS = {
    "vda_bridge_cancel_*.flag": "worker_cancel_marker",
    "vda_ade_manifest_*": "ade_manifest_temp",
    "vda_mos_characterization_*": "mos_characterization_temp",
    "vda_inverter_*": "inverter_simulation_temp",
    "vda_common_source_*": "common_source_simulation_temp",
    "vda_differential_pair_*": "differential_pair_simulation_temp",
}


def load_retention_pins(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("pins"), list):
        raise ValueError("retention pin manifest must use schema_version=1 and pins[]")
    pins: dict[str, str] = {}
    for item in payload["pins"]:
        if not isinstance(item, dict):
            raise ValueError("each retention pin must be an object")
        raw_path = str(item.get("path") or "").strip().replace("\\", "/")
        reason = str(item.get("reason") or "").strip()
        if not raw_path or not reason or any(char in raw_path for char in "*?[]"):
            raise ValueError("each retention pin needs an exact path and reason")
        if raw_path.startswith("/"):
            normalized = str(PurePosixPath(raw_path))
            if not normalized.startswith("/data/xum/") or normalized.count("/") < 4:
                raise ValueError("remote retention pins must be exact paths below /data/xum")
        else:
            candidate = PurePosixPath(raw_path)
            if candidate.is_absolute() or ".." in candidate.parts or len(candidate.parts) != 1:
                raise ValueError("local retention pins must name one artifact-root entry")
            normalized = candidate.as_posix()
        if normalized in pins:
            raise ValueError(f"duplicate retention pin: {normalized}")
        pins[normalized] = reason
    return pins


def _tree_size(path: Path) -> tuple[int, int, int]:
    if path.is_symlink():
        return path.lstat().st_size, 0, 0
    if path.is_file():
        return path.stat().st_size, 1, 0
    total = 0
    files = 0
    directories = 1
    for root, dirnames, filenames in os.walk(path):
        directories += len(dirnames)
        for name in filenames:
            candidate = Path(root) / name
            try:
                total += candidate.stat().st_size
                files += 1
            except OSError:
                continue
    return total, files, directories


def audit_local_resources(
    artifact_root: Path,
    *,
    older_than_days: float,
    pins: dict[str, str] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Inventory top-level evidence and VDA cancellation markers without deleting."""

    if older_than_days < 0:
        raise ValueError("older-than-days must be non-negative")
    observed_at = time.time() if now is None else now
    pin_map = pins or {}
    entries: list[dict[str, Any]] = []
    if artifact_root.is_dir():
        for path in sorted(artifact_root.iterdir(), key=lambda item: item.name.lower()):
            stat = path.stat()
            size_bytes, file_count, directory_count = _tree_size(path)
            age_days = max(0.0, (observed_at - stat.st_mtime) / 86400.0)
            reason = pin_map.get(path.name)
            entries.append(
                {
                    "path": str(path.resolve()),
                    "artifact_root_entry": path.name,
                    "kind": "directory" if path.is_dir() else "file",
                    "size_bytes": size_bytes,
                    "file_count": file_count,
                    "directory_count": directory_count,
                    "modified_epoch": stat.st_mtime,
                    "age_days": age_days,
                    "pinned": reason is not None,
                    "pin_reason": reason,
                    "review_candidate": age_days >= older_than_days and reason is None,
                    "delete_authorized": False,
                    "evidence_source": "software_inference",
                }
            )

    temp_root = Path(tempfile.gettempdir())
    transient_entries: list[dict[str, Any]] = []
    seen_transient_paths: set[Path] = set()
    for pattern, kind in _TRANSIENT_PATTERNS.items():
        for path in sorted(temp_root.glob(pattern)):
            if path in seen_transient_paths:
                continue
            seen_transient_paths.add(path)
            try:
                stat = path.lstat()
                size_bytes, file_count, directory_count = _tree_size(path)
            except OSError:
                continue
            transient_entries.append(
                {
                    "path": str(path.resolve()),
                    "kind": kind,
                    "age_days": max(
                        0.0, (observed_at - stat.st_mtime) / 86400.0
                    ),
                    "size_bytes": size_bytes,
                    "file_count": file_count,
                    "directory_count": directory_count,
                    "delete_authorized": False,
                    "evidence_source": "system_event",
                }
            )
    transient_entries.sort(key=lambda item: item["path"].lower())
    cancel_markers = [
        item for item in transient_entries if item["kind"] == "worker_cancel_marker"
    ]

    return {
        "artifact_root": str(artifact_root.resolve()),
        "entries": entries,
        "entry_count": len(entries),
        "total_size_bytes": sum(item["size_bytes"] for item in entries),
        "review_candidate_count": sum(item["review_candidate"] for item in entries),
        "transient_entries": transient_entries,
        "transient_entry_count": len(transient_entries),
        "transient_total_size_bytes": sum(
            item["size_bytes"] for item in transient_entries
        ),
        "transient_cancel_markers": cancel_markers,
        "transient_cancel_marker_count": len(cancel_markers),
        "older_than_days": older_than_days,
        "deletion_performed": False,
        "evidence_source": "software_inference",
    }
