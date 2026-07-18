"""Deterministic non-EDA adapter for proving orchestration behavior."""

from __future__ import annotations

from typing import Any

from ..models import EvidenceSource, TaskSpec
from .base import AdapterResult


class DeterministicDemoAdapter:
    name = "deterministic-demo"

    def __init__(self) -> None:
        self._schematics: dict[tuple[str, str], dict[str, Any]] = {}

    def probe(self, pdk_profile: str) -> AdapterResult:
        return AdapterResult(
            data={
                "ok": True,
                "profile": pdk_profile,
                "warning": "demo adapter; no Bridge or EDA was contacted",
            },
            evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
        )

    def _key(self, task: TaskSpec) -> tuple[str, str]:
        return task.target.library, task.target.cell

    def create_schematic(self, task: TaskSpec) -> AdapterResult:
        key = self._key(task)
        existing = key in self._schematics
        if not existing:
            self._schematics[key] = {
                "instances": ["MN0", "MP0"],
                "nets": ["IN", "OUT", "VDD", "VSS"],
                "pins": ["IN", "OUT", "VDD", "VSS"],
                "parameters": dict(task.parameters),
            }
        return AdapterResult(
            data={"created": not existing, "already_exists": existing},
            evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
        )

    def inspect_schematic(self, task: TaskSpec) -> AdapterResult:
        schematic = self._schematics.get(self._key(task))
        if schematic is None:
            raise RuntimeError("demo schematic does not exist")
        return AdapterResult(
            data=dict(schematic),
            evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
        )

    def apply_parameters(
        self, task: TaskSpec, parameters: dict[str, float]
    ) -> AdapterResult:
        schematic = self._schematics.get(self._key(task))
        if schematic is None:
            raise RuntimeError("demo schematic does not exist")
        schematic["parameters"] = dict(parameters)
        return AdapterResult(
            data={"applied": dict(parameters)},
            evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
        )

    def simulate(
        self, task: TaskSpec, parameters: dict[str, float]
    ) -> AdapterResult:
        wn = parameters.get("nmos_width_um", 0.5)
        wp = parameters.get("pmos_width_um", 1.0)
        load = parameters.get("load_ff", 2.0)
        vdd = parameters.get("vdd_v", 0.9)
        drive = max(wn + 0.55 * wp, 0.05)
        ratio_penalty = 5.0 * abs(wp / max(wn, 0.05) - 2.0)
        delay = 12.0 + 18.0 * load / drive + ratio_penalty
        rise = 8.0 + 12.0 * load / max(wp, 0.05)
        fall = 8.0 + 8.0 * load / max(wn, 0.05)
        return AdapterResult(
            data={
                "metrics": {
                    "delay_ps": delay,
                    "tphl_ps": delay + 0.25 * (fall - rise),
                    "tplh_ps": delay - 0.25 * (fall - rise),
                    "rise_ps": rise,
                    "fall_ps": fall,
                    "rise_fall_skew_ps": abs(rise - fall),
                    "voh_v": 0.995 * vdd,
                    "vol_v": 0.005 * vdd,
                },
                "warning": "analytical demo only; not an EDA result",
            },
            evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
        )
