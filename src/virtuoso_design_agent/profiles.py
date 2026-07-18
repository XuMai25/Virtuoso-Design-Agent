"""Load versioned PDK facts shipped with the agent."""

from __future__ import annotations

import json
from importlib.resources import files

from .models import PdkProfile


def load_pdk_profile(name: str) -> PdkProfile:
    resource = files("virtuoso_design_agent").joinpath(
        "resources", "pdks", f"{name}.json"
    )
    if not resource.is_file():
        raise ValueError(f"unknown PDK profile: {name}")
    return PdkProfile.model_validate(json.loads(resource.read_text(encoding="utf-8")))
