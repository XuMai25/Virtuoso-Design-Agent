"""Load versioned PDK facts shipped with the agent."""

from __future__ import annotations

import json
from importlib.resources import files

from .models import PdkProfile


def _load_pdk_profile_payload(name: str, stack: tuple[str, ...]) -> dict:
    if name in stack:
        raise ValueError("cyclic PDK profile inheritance: " + " -> ".join((*stack, name)))
    resource = files("virtuoso_design_agent").joinpath(
        "resources", "pdks", f"{name}.json"
    )
    if not resource.is_file():
        raise ValueError(f"unknown PDK profile: {name}")
    payload = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"PDK profile {name} must be a JSON object")
    parent = payload.pop("extends", None)
    if parent is None:
        return payload
    if not isinstance(parent, str) or not parent:
        raise ValueError(f"PDK profile {name} has invalid extends value")
    return _load_pdk_profile_payload(parent, (*stack, name)) | payload


def load_pdk_profile(name: str) -> PdkProfile:
    profile = PdkProfile.model_validate(_load_pdk_profile_payload(name, ()))
    if profile.name != name:
        raise ValueError(
            f"PDK profile resource {name!r} declares name {profile.name!r}"
        )
    return profile
