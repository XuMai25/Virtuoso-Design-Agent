"""Deterministic non-EDA adapter for proving orchestration behavior."""

from __future__ import annotations

from typing import Any

from ..metrics import extract_common_source_dc_metrics
from ..models import CircuitKind, EvidenceSource, TaskSpec
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

    @staticmethod
    def _semantic_parameters(task: TaskSpec) -> dict[str, float]:
        if task.circuit is CircuitKind.COMMON_SOURCE:
            return {
                "device_width_um": float(task.parameters.get("device_width_um", 1.0)),
                "length_um": float(task.parameters.get("length_um", 0.03)),
                "load_resistance_ohm": float(
                    task.parameters.get("load_resistance_ohm", 20_000.0)
                ),
            }
        return {
            "nmos_width_um": float(task.parameters.get("nmos_width_um", 0.5)),
            "pmos_width_um": float(task.parameters.get("pmos_width_um", 1.0)),
            "length_um": float(task.parameters.get("length_um", 0.03)),
        }

    def create_schematic(self, task: TaskSpec) -> AdapterResult:
        key = self._key(task)
        existing = key in self._schematics
        if not existing:
            semantic_parameters = self._semantic_parameters(task)
            common_source = task.circuit is CircuitKind.COMMON_SOURCE
            self._schematics[key] = {
                "instances": ["MN0", "RD0"] if common_source else ["MN0", "MP0"],
                "nets": ["IN", "OUT", "VDD", "VSS"],
                "pins": ["IN", "OUT", "VDD", "VSS"],
                "parameters": dict(task.parameters) | semantic_parameters,
                "semantic_parameters": semantic_parameters,
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
            data={
                **schematic,
                "parameters": dict(schematic["parameters"]),
                "semantic_parameters": dict(schematic["semantic_parameters"]),
            },
            evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
        )

    def apply_parameters(
        self, task: TaskSpec, parameters: dict[str, float]
    ) -> AdapterResult:
        schematic = self._schematics.get(self._key(task))
        if schematic is None:
            raise RuntimeError("demo schematic does not exist")
        schematic["parameters"].update(parameters)
        semantic_names = (
            ("device_width_um", "length_um", "load_resistance_ohm")
            if task.circuit is CircuitKind.COMMON_SOURCE
            else ("nmos_width_um", "pmos_width_um", "length_um")
        )
        for name in semantic_names:
            if name in parameters:
                schematic["semantic_parameters"][name] = float(parameters[name])
        return AdapterResult(
            data={
                "applied": dict(parameters),
                "semantic_parameters": dict(schematic["semantic_parameters"]),
            },
            evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
        )

    def simulate(
        self, task: TaskSpec, parameters: dict[str, float]
    ) -> AdapterResult:
        schematic = self._schematics.get(self._key(task))
        if schematic is None:
            raise RuntimeError("demo schematic does not exist")
        effective_parameters = dict(parameters)
        effective_parameters.update(schematic["semantic_parameters"])
        if task.circuit is CircuitKind.COMMON_SOURCE:
            width = effective_parameters["device_width_um"]
            length = effective_parameters["length_um"]
            resistance = effective_parameters["load_resistance_ohm"]
            bias = effective_parameters.get("bias_v", 0.45)
            vdd = effective_parameters.get("vdd_v", 0.9)
            overdrive = max(bias - 0.25, 0.0)
            drain_current_a = (
                100.0 * width * (0.03 / length) * overdrive * 1e-6
            )
            vout = vdd - drain_current_a * resistance
            gm_s = 2.0 * drain_current_a / max(overdrive, 0.01)
            gds_s = max(gm_s / 20.0, 1e-9)
            metrics = extract_common_source_dc_metrics(
                vdd_v=vdd,
                vin_v=bias,
                vout_v=vout,
                vss_v=0.0,
                drain_current_a=drain_current_a,
                vdsat_v=overdrive,
                gm_s=gm_s,
                gds_s=gds_s,
                load_resistance_ohm=resistance,
            )
            return AdapterResult(
                data={
                    "parameters": effective_parameters,
                    "metrics": metrics,
                    "metric_sources": {
                        name: EvidenceSource.SOFTWARE_INFERENCE.value
                        for name in metrics
                    },
                    "warning": "analytical demo only; not an EDA result",
                },
                evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
            )
        wn = effective_parameters["nmos_width_um"]
        wp = effective_parameters["pmos_width_um"]
        load = effective_parameters.get("load_ff", 2.0)
        vdd = effective_parameters.get("vdd_v", 0.9)
        drive = max(wn + 0.55 * wp, 0.05)
        ratio_penalty = 5.0 * abs(wp / max(wn, 0.05) - 2.0)
        delay = 12.0 + 18.0 * load / drive + ratio_penalty
        rise = 8.0 + 12.0 * load / max(wp, 0.05)
        fall = 8.0 + 8.0 * load / max(wn, 0.05)
        gate_area_proxy = (wn + wp) * effective_parameters["length_um"]
        metrics = {
            "delay_ps": delay,
            "tphl_ps": delay + 0.25 * (fall - rise),
            "tplh_ps": delay - 0.25 * (fall - rise),
            "rise_ps": rise,
            "fall_ps": fall,
            "rise_fall_skew_ps": abs(rise - fall),
            "voh_v": 0.995 * vdd,
            "vol_v": 0.005 * vdd,
            "gate_area_proxy_um2": gate_area_proxy,
        }
        return AdapterResult(
            data={
                "parameters": effective_parameters,
                "metrics": metrics,
                "metric_sources": {
                    name: EvidenceSource.SOFTWARE_INFERENCE.value for name in metrics
                },
                "warning": "analytical demo only; not an EDA result",
            },
            evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
        )
