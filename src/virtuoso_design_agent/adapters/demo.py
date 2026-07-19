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
        if task.circuit is CircuitKind.EXISTING_SCHEMATIC:
            return {}
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

    @staticmethod
    def _instance_parameters(
        task: TaskSpec, semantic_parameters: dict[str, float]
    ) -> dict[str, dict[str, str]]:
        if task.circuit is CircuitKind.EXISTING_SCHEMATIC:
            return {
                update.instance: {}
                for update in task.instance_parameter_updates
            }
        if task.circuit is CircuitKind.COMMON_SOURCE:
            return {
                "MN0": {
                    "Wfg": f"{semantic_parameters['device_width_um']:.12g}u",
                    "l": f"{semantic_parameters['length_um']:.12g}u",
                    "fingers": "1",
                    "m": "1",
                },
                "RD0": {
                    "r": f"{semantic_parameters['load_resistance_ohm']:.12g}"
                },
            }
        return {
            "MN0": {
                "Wfg": f"{semantic_parameters['nmos_width_um']:.12g}u",
                "l": f"{semantic_parameters['length_um']:.12g}u",
                "fingers": "1",
                "m": "1",
            },
            "MP0": {
                "Wfg": f"{semantic_parameters['pmos_width_um']:.12g}u",
                "l": f"{semantic_parameters['length_um']:.12g}u",
                "fingers": "1",
                "m": "1",
            },
        }

    @staticmethod
    def _applied_instance_parameters(
        requested: dict[str, dict[str, str]],
    ) -> dict[str, dict[str, str]]:
        applied: dict[str, dict[str, str]] = {}
        for instance, parameters in requested.items():
            if "w" in parameters and "wf" in parameters:
                raise ValueError("Specify w (total width) or wf (finger width), not both")
            resolved = {
                ({"wf": "Wfg", "nf": "fingers"}.get(name, name)): value
                for name, value in parameters.items()
            }
            applied[instance] = resolved
        return applied

    def create_schematic(self, task: TaskSpec) -> AdapterResult:
        key = self._key(task)
        existing = key in self._schematics
        if not existing:
            semantic_parameters = self._semantic_parameters(task)
            common_source = task.circuit is CircuitKind.COMMON_SOURCE
            instance_parameters = self._instance_parameters(
                task, semantic_parameters
            )
            self._schematics[key] = {
                "instances": (
                    list(instance_parameters)
                    if task.circuit is CircuitKind.EXISTING_SCHEMATIC
                    else ["MN0", "RD0"] if common_source else ["MN0", "MP0"]
                ),
                "nets": (
                    []
                    if task.circuit is CircuitKind.EXISTING_SCHEMATIC
                    else ["IN", "OUT", "VDD", "VSS"]
                ),
                "pins": (
                    []
                    if task.circuit is CircuitKind.EXISTING_SCHEMATIC
                    else ["IN", "OUT", "VDD", "VSS"]
                ),
                "parameters": dict(task.parameters) | semantic_parameters,
                "semantic_parameters": semantic_parameters,
                "instance_parameters": instance_parameters,
            }
        return AdapterResult(
            data={"created": not existing, "already_exists": existing},
            evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
        )

    def inspect_schematic(self, task: TaskSpec) -> AdapterResult:
        schematic = self._schematics.get(self._key(task))
        if schematic is None:
            raise RuntimeError("demo schematic does not exist")
        data = {
            **schematic,
            "parameters": dict(schematic["parameters"]),
            "semantic_parameters": dict(schematic["semantic_parameters"]),
            "instance_parameters": {
                instance: dict(parameters)
                for instance, parameters in schematic["instance_parameters"].items()
            },
        }
        return AdapterResult(
            data=data,
            evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
        )

    def verify_parameters(
        self, task: TaskSpec, expected: dict[str, dict[str, str]]
    ) -> AdapterResult:
        result = self.inspect_schematic(task)
        data = dict(result.data)
        for instance, parameters in expected.items():
            if instance not in data["instance_parameters"]:
                raise RuntimeError(
                    f"parameter confirmation is missing instance {instance}"
                )
            for name, value in parameters.items():
                if data["instance_parameters"][instance].get(name) != value:
                    raise RuntimeError(
                        f"parameter confirmation mismatch for {instance}.{name}"
                    )
        data["confirmed_instance_parameters"] = expected
        data["confirmed_evidence_source"] = "software_inference"
        data["confirmation_method"] = "demo_exact_value_equality"
        return AdapterResult(
            data=data,
            evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
        )

    def apply_parameters(
        self, task: TaskSpec, parameters: dict[str, float]
    ) -> AdapterResult:
        schematic = self._schematics.get(self._key(task))
        if schematic is None:
            raise RuntimeError("demo schematic does not exist")
        result_data: dict[str, Any] = {}
        if parameters:
            schematic["parameters"].update(parameters)
            semantic_names = (
                ("device_width_um", "length_um", "load_resistance_ohm")
                if task.circuit is CircuitKind.COMMON_SOURCE
                else (
                    ()
                    if task.circuit is CircuitKind.EXISTING_SCHEMATIC
                    else ("nmos_width_um", "pmos_width_um", "length_um")
                )
            )
            for name in semantic_names:
                if name in parameters:
                    schematic["semantic_parameters"][name] = float(parameters[name])
            result_data.update(
                {
                    "applied": dict(parameters),
                    "semantic_parameters": dict(schematic["semantic_parameters"]),
                }
            )
        if task.instance_parameter_updates:
            requested = {
                update.instance: dict(update.parameters)
                for update in task.instance_parameter_updates
            }
            applied = self._applied_instance_parameters(requested)
            missing = sorted(set(applied) - set(schematic["instance_parameters"]))
            if missing:
                raise RuntimeError(
                    "explicit parameter write targets missing instances: "
                    + ", ".join(missing)
                )
            before = {
                instance: {
                    name: schematic["instance_parameters"][instance].get(name)
                    for name in parameters
                }
                for instance, parameters in applied.items()
            }
            for instance, parameters in applied.items():
                schematic["instance_parameters"][instance].update(parameters)
            confirmed = {
                instance: {
                    name: schematic["instance_parameters"][instance][name]
                    for name in parameters
                }
                for instance, parameters in applied.items()
            }
            result_data.update(
                {
                    "requested_instance_parameters": requested,
                    "requested_evidence_source": "user_input",
                    "applied_instance_parameters": applied,
                    "before_instance_parameters": before,
                    "confirmed_instance_parameters": confirmed,
                    "confirmed_evidence_source": "software_inference",
                    "confirmation_method": "demo_exact_value_equality",
                }
            )
        return AdapterResult(
            data=result_data,
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
