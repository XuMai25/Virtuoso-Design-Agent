"""Deterministic non-EDA adapter for proving orchestration behavior."""

from __future__ import annotations

import math
from typing import Any

from ..metrics import (
    aggregate_common_source_linearity_metrics,
    aggregate_differential_pair_linearity_metrics,
    extract_common_source_ac_metrics,
    extract_common_source_dc_metrics,
    extract_common_source_noise_metrics,
    extract_differential_pair_ac_metrics,
    extract_differential_pair_cmrr_response_metrics,
    extract_differential_pair_common_mode_ac_metrics,
    extract_differential_pair_dc_metrics,
)
from ..models import (
    AnalysisKind,
    CircuitKind,
    EvidenceSource,
    SchematicTransformAction,
    TaskSpec,
)
from .base import AdapterResult, merge_analysis_bundle


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
        if task.circuit is CircuitKind.DIFFERENTIAL_PAIR:
            return {
                "input_width_um": float(
                    task.parameters.get("input_width_um", 1.0)
                ),
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
            parameters = {
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
            if "source_resistance_ohm" in semantic_parameters:
                parameters["RS0"] = {
                    "r": f"{semantic_parameters['source_resistance_ohm']:.12g}"
                }
            return parameters
        if task.circuit is CircuitKind.DIFFERENTIAL_PAIR:
            mos = {
                "Wfg": f"{semantic_parameters['input_width_um']:.12g}u",
                "l": f"{semantic_parameters['length_um']:.12g}u",
                "fingers": "1",
                "m": "1",
            }
            resistor = {
                "r": f"{semantic_parameters['load_resistance_ohm']:.12g}"
            }
            return {
                "MN0": dict(mos),
                "MN1": dict(mos),
                "RD0": dict(resistor),
                "RD1": dict(resistor),
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
            differential_pair = task.circuit is CircuitKind.DIFFERENTIAL_PAIR
            instance_parameters = self._instance_parameters(
                task, semantic_parameters
            )
            self._schematics[key] = {
                "instances": (
                    list(instance_parameters)
                    if task.circuit is CircuitKind.EXISTING_SCHEMATIC
                    else (
                        [
                            {
                                "name": "MN0",
                                "library": "demo_pdk",
                                "cell": "nmos",
                                "parameters": dict(instance_parameters["MN0"]),
                                "terminals": {
                                    "D": "OUT",
                                    "G": "IN",
                                    "S": "VSS",
                                    "B": "VSS",
                                },
                                "xy": [0.0, 0.0],
                                "orient": "R0",
                            },
                            {
                                "name": "RD0",
                                "library": "analogLib",
                                "cell": "res",
                                "parameters": dict(instance_parameters["RD0"]),
                                "terminals": {"PLUS": "VDD", "MINUS": "OUT"},
                                "xy": [0.0, 1.3],
                                "orient": "R0",
                            },
                        ]
                        if common_source
                        else (
                            [
                                {
                                    "name": "MN0",
                                    "library": "demo_pdk",
                                    "cell": "nmos",
                                    "parameters": dict(instance_parameters["MN0"]),
                                    "terminals": {
                                        "D": "OUTP",
                                        "G": "INP",
                                        "S": "TAIL",
                                        "B": "VSS",
                                    },
                                    "xy": [-0.8, 0.0],
                                    "orient": "R0",
                                },
                                {
                                    "name": "MN1",
                                    "library": "demo_pdk",
                                    "cell": "nmos",
                                    "parameters": dict(instance_parameters["MN1"]),
                                    "terminals": {
                                        "D": "OUTN",
                                        "G": "INN",
                                        "S": "TAIL",
                                        "B": "VSS",
                                    },
                                    "xy": [0.8, 0.0],
                                    "orient": "R0",
                                },
                                {
                                    "name": "RD0",
                                    "library": "analogLib",
                                    "cell": "res",
                                    "parameters": dict(instance_parameters["RD0"]),
                                    "terminals": {
                                        "PLUS": "VDD",
                                        "MINUS": "OUTP",
                                    },
                                    "xy": [-0.8, 1.3],
                                    "orient": "R0",
                                },
                                {
                                    "name": "RD1",
                                    "library": "analogLib",
                                    "cell": "res",
                                    "parameters": dict(instance_parameters["RD1"]),
                                    "terminals": {
                                        "PLUS": "VDD",
                                        "MINUS": "OUTN",
                                    },
                                    "xy": [0.8, 1.3],
                                    "orient": "R0",
                                },
                            ]
                            if differential_pair
                            else [
                                {
                                    "name": "MN0",
                                    "library": "demo_pdk",
                                    "cell": "nmos",
                                    "parameters": dict(instance_parameters["MN0"]),
                                    "terminals": {
                                        "D": "OUT",
                                        "G": "IN",
                                        "S": "VSS",
                                        "B": "VSS",
                                    },
                                    "xy": [0.0, 0.0],
                                    "orient": "R0",
                                },
                                {
                                    "name": "MP0",
                                    "library": "demo_pdk",
                                    "cell": "pmos",
                                    "parameters": dict(instance_parameters["MP0"]),
                                    "terminals": {
                                        "D": "OUT",
                                        "G": "IN",
                                        "S": "VDD",
                                        "B": "VDD",
                                    },
                                    "xy": [0.0, 1.0],
                                    "orient": "R0",
                                },
                            ]
                        )
                    )
                ),
                "nets": (
                    []
                    if task.circuit is CircuitKind.EXISTING_SCHEMATIC
                    else (
                        ["INP", "INN", "OUTP", "OUTN", "TAIL", "VDD", "VSS"]
                        if differential_pair
                        else ["IN", "OUT", "VDD", "VSS"]
                    )
                ),
                "pins": (
                    []
                    if task.circuit is CircuitKind.EXISTING_SCHEMATIC
                    else (
                        ["INP", "INN", "OUTP", "OUTN", "TAIL", "VDD", "VSS"]
                        if differential_pair
                        else ["IN", "OUT", "VDD", "VSS"]
                    )
                ),
                "parameters": dict(task.parameters) | semantic_parameters,
                "semantic_parameters": semantic_parameters,
                "instance_parameters": instance_parameters,
                "topology_variant": (
                    "common_source"
                    if common_source
                    else "resistive_load_nmos_differential_pair"
                    if differential_pair
                    else task.circuit.value
                ),
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
            "instances": [
                (
                    {
                        **item,
                        "parameters": dict(
                            schematic["instance_parameters"].get(
                                str(item.get("name")), item.get("parameters", {})
                            )
                        ),
                        "terminals": dict(item.get("terminals", {})),
                    }
                    if isinstance(item, dict)
                    else item
                )
                for item in schematic["instances"]
            ],
            "nets": list(schematic["nets"]),
            "pins": list(schematic["pins"]),
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

    def transform_schematic(self, task: TaskSpec) -> AdapterResult:
        schematic = self._schematics.get(self._key(task))
        if schematic is None:
            raise RuntimeError("demo schematic does not exist")
        if task.circuit is CircuitKind.INVERTER:
            variant = schematic.get("topology_variant")
            changed = variant == "inverter"
            if variant not in {"inverter", "inverter_testbench"}:
                raise RuntimeError(f"unsupported demo topology variant: {variant}")
            vdd_v = float(task.parameters["vdd_v"])
            load_ff = float(task.parameters["load_ff"])
            if changed:
                for item in schematic["instances"]:
                    if item["name"] == "MN0":
                        item["terminals"].update({"S": "gnd!", "B": "gnd!"})
                schematic["instances"].extend(
                    [
                        {
                            "name": "VDD0",
                            "library": "analogLib",
                            "cell": "vdc",
                            "parameters": {},
                            "terminals": {"PLUS": "VDD", "MINUS": "gnd!"},
                        },
                        {
                            "name": "VIN0",
                            "library": "analogLib",
                            "cell": "vpulse",
                            "parameters": {},
                            "terminals": {"PLUS": "IN", "MINUS": "gnd!"},
                        },
                        {
                            "name": "CL0",
                            "library": "analogLib",
                            "cell": "cap",
                            "parameters": {},
                            "terminals": {"PLUS": "OUT", "MINUS": "gnd!"},
                        },
                        {
                            "name": "GND0",
                            "library": "analogLib",
                            "cell": "gnd",
                            "parameters": {},
                            "terminals": {"gnd!": "gnd!"},
                        },
                    ]
                )
                schematic["nets"] = sorted(set(schematic["nets"]) | {"gnd!"})
                schematic["topology_variant"] = "inverter_testbench"
            source_parameters = {
                "VDD0": {"vdc": f"{vdd_v:.12g}", "srcType": "dc"},
                "VIN0": {
                    "v1": "0",
                    "v2": f"{vdd_v:.12g}",
                    "per": "100p",
                    "td": "0",
                    "tr": "5p",
                    "tf": "5p",
                    "pw": "50p",
                    "srcType": "pulse",
                },
                "CL0": {"c": f"{load_ff:.12g}f"},
                "GND0": {},
            }
            schematic["instance_parameters"].update(source_parameters)
            schematic["parameters"].update({"vdd_v": vdd_v, "load_ff": load_ff})
            return AdapterResult(
                data={
                    "transformed": changed,
                    "already_transformed": not changed,
                    "requested_testbench_parameters": {
                        "vdd_v": vdd_v,
                        "load_ff": load_ff,
                    },
                },
                evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
            )
        if task.circuit is CircuitKind.DIFFERENTIAL_PAIR:
            variant = schematic.get("topology_variant")
            supported = {
                "resistive_load_nmos_differential_pair",
                "resistive_load_nmos_differential_pair_with_tail_device",
            }
            if variant not in supported:
                raise RuntimeError(f"unsupported demo topology variant: {variant}")
            changed = variant == "resistive_load_nmos_differential_pair"
            tail_width_um = float(task.parameters["tail_width_um"])
            tail_length_um = float(task.parameters["tail_length_um"])
            if changed:
                schematic["instances"].append(
                    {
                        "name": "MNTAIL",
                        "library": "demo_pdk",
                        "cell": "nmos",
                        "parameters": {},
                        "terminals": {
                            "D": "TAIL",
                            "G": "BIAS",
                            "S": "VSS",
                            "B": "VSS",
                        },
                        "xy": [0.0, -1.6],
                        "orient": "R0",
                    }
                )
                schematic["nets"] = sorted(set(schematic["nets"]) | {"BIAS"})
                schematic["pins"] = sorted(set(schematic["pins"]) | {"BIAS"})
                schematic["topology_variant"] = (
                    "resistive_load_nmos_differential_pair_with_tail_device"
                )
            tail_parameters = {
                "Wfg": f"{tail_width_um:.12g}u",
                "l": f"{tail_length_um:.12g}u",
                "fingers": "1",
                "m": "1",
            }
            schematic["instance_parameters"]["MNTAIL"] = tail_parameters
            for item in schematic["instances"]:
                if isinstance(item, dict) and item.get("name") == "MNTAIL":
                    item["parameters"] = dict(tail_parameters)
            semantic = {
                "tail_width_um": tail_width_um,
                "tail_length_um": tail_length_um,
            }
            schematic["semantic_parameters"].update(semantic)
            schematic["parameters"].update(semantic)
            return AdapterResult(
                data={
                    "transformed": changed,
                    "already_transformed": not changed,
                    "transform_action": task.resolved_schematic_transform_action().value,
                    "topology_delta": {
                        "added_instance": "MNTAIL" if changed else None,
                        "added_pin": "BIAS" if changed else None,
                        "added_net": "BIAS" if changed else None,
                    },
                    "readback": self.inspect_schematic(task).data,
                },
                evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
            )
        if task.circuit is not CircuitKind.COMMON_SOURCE:
            raise RuntimeError(
                "demo transform supports inverter, common_source, or differential_pair"
            )
        variant = schematic.get("topology_variant")
        if variant not in {"common_source", "source_degenerated_common_source"}:
            raise RuntimeError(f"unsupported demo topology variant: {variant}")
        transform_action = task.resolved_schematic_transform_action()
        if transform_action is SchematicTransformAction.REMOVE_SOURCE_DEGENERATION:
            changed = variant == "source_degenerated_common_source"
            if changed:
                for item in schematic["instances"]:
                    if isinstance(item, dict) and item.get("name") == "MN0":
                        item["terminals"]["S"] = "VSS"
                schematic["instances"] = [
                    item
                    for item in schematic["instances"]
                    if not isinstance(item, dict) or item.get("name") != "RS0"
                ]
                schematic["nets"] = sorted(set(schematic["nets"]) - {"NSRC"})
                schematic["topology_variant"] = "common_source"
                schematic["semantic_parameters"].pop(
                    "source_resistance_ohm", None
                )
                schematic["parameters"].pop("source_resistance_ohm", None)
                schematic["instance_parameters"].pop("RS0", None)
            return AdapterResult(
                data={
                    "transformed": changed,
                    "already_removed": not changed,
                    "transform_action": transform_action.value,
                    "topology_delta": {
                        "renamed_terminal_net": (
                            "MN0.S: NSRC -> VSS" if changed else None
                        ),
                        "removed_instance": "RS0" if changed else None,
                        "removed_net": "NSRC" if changed else None,
                    },
                    "readback": self.inspect_schematic(task).data,
                },
                evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
            )

        resistance = float(task.parameters["source_resistance_ohm"])
        changed = variant == "common_source"
        if changed:
            for item in schematic["instances"]:
                if isinstance(item, dict) and item.get("name") == "MN0":
                    item["terminals"]["S"] = "NSRC"
            schematic["instances"].append(
                {
                    "name": "RS0",
                    "library": "analogLib",
                    "cell": "res",
                    "parameters": {"r": f"{resistance:.12g}"},
                    "terminals": {"PLUS": "NSRC", "MINUS": "VSS"},
                    "xy": [0.0, -1.3],
                    "orient": "R0",
                }
            )
            schematic["nets"] = sorted(set(schematic["nets"]) | {"NSRC"})
            schematic["topology_variant"] = "source_degenerated_common_source"
        previous = schematic["semantic_parameters"].get("source_resistance_ohm")
        schematic["semantic_parameters"]["source_resistance_ohm"] = resistance
        schematic["parameters"]["source_resistance_ohm"] = resistance
        schematic["instance_parameters"]["RS0"] = {
            "r": f"{resistance:.12g}"
        }
        return AdapterResult(
            data={
                "transformed": changed,
                "already_transformed": not changed,
                "transform_action": transform_action.value,
                "resistance_changed": previous is None or previous != resistance,
                "topology_delta": {
                    "renamed_terminal_net": "MN0.S: VSS -> NSRC" if changed else None,
                    "added_instance": "RS0" if changed else None,
                    "added_net": "NSRC" if changed else None,
                },
                "readback": self.inspect_schematic(task).data,
            },
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
            if (
                task.circuit is CircuitKind.COMMON_SOURCE
                and "source_resistance_ohm" in parameters
                and "RS0" not in schematic["instance_parameters"]
            ):
                raise RuntimeError(
                    "source_resistance_ohm requires source-degenerated topology"
                )
            if (
                task.circuit is CircuitKind.DIFFERENTIAL_PAIR
                and {"tail_width_um", "tail_length_um"} & parameters.keys()
                and "MNTAIL" not in schematic["instance_parameters"]
            ):
                raise RuntimeError(
                    "tail_width_um/tail_length_um require the real-tail topology"
                )
            schematic["parameters"].update(parameters)
            semantic_names = (
                (
                    "device_width_um",
                    "length_um",
                    "load_resistance_ohm",
                    "source_resistance_ohm",
                )
                if task.circuit is CircuitKind.COMMON_SOURCE
                else (
                    (
                        "input_width_um",
                        "length_um",
                        "load_resistance_ohm",
                        "tail_width_um",
                        "tail_length_um",
                    )
                    if task.circuit is CircuitKind.DIFFERENTIAL_PAIR
                    else (
                        ()
                        if task.circuit is CircuitKind.EXISTING_SCHEMATIC
                        else ("nmos_width_um", "pmos_width_um", "length_um")
                    )
                )
            )
            for name in semantic_names:
                if name in parameters:
                    schematic["semantic_parameters"][name] = float(parameters[name])
            if task.circuit is CircuitKind.COMMON_SOURCE:
                if "device_width_um" in parameters:
                    schematic["instance_parameters"]["MN0"]["Wfg"] = (
                        f"{float(parameters['device_width_um']):.12g}u"
                    )
                if "length_um" in parameters:
                    schematic["instance_parameters"]["MN0"]["l"] = (
                        f"{float(parameters['length_um']):.12g}u"
                    )
                if "load_resistance_ohm" in parameters:
                    schematic["instance_parameters"]["RD0"]["r"] = (
                        f"{float(parameters['load_resistance_ohm']):.12g}"
                    )
                if "source_resistance_ohm" in parameters:
                    schematic["instance_parameters"]["RS0"]["r"] = (
                        f"{float(parameters['source_resistance_ohm']):.12g}"
                    )
            elif task.circuit is CircuitKind.DIFFERENTIAL_PAIR:
                for instance in ("MN0", "MN1"):
                    if "input_width_um" in parameters:
                        schematic["instance_parameters"][instance]["Wfg"] = (
                            f"{float(parameters['input_width_um']):.12g}u"
                        )
                    if "length_um" in parameters:
                        schematic["instance_parameters"][instance]["l"] = (
                            f"{float(parameters['length_um']):.12g}u"
                        )
                if "load_resistance_ohm" in parameters:
                    resistance = f"{float(parameters['load_resistance_ohm']):.12g}"
                    for instance in ("RD0", "RD1"):
                        schematic["instance_parameters"][instance]["r"] = resistance
                if "tail_width_um" in parameters:
                    schematic["instance_parameters"]["MNTAIL"]["Wfg"] = (
                        f"{float(parameters['tail_width_um']):.12g}u"
                    )
                if "tail_length_um" in parameters:
                    schematic["instance_parameters"]["MNTAIL"]["l"] = (
                        f"{float(parameters['tail_length_um']):.12g}u"
                    )
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

    def prepare_ade(self, task: TaskSpec) -> AdapterResult:
        raise RuntimeError(
            "ade.prepare requires the real Bridge; the demo adapter cannot create "
            "or verify a persistent Maestro view"
        )

    def capture_ade(self, task: TaskSpec) -> AdapterResult:
        raise RuntimeError(
            "ade.capture requires the real Bridge; the demo adapter cannot "
            "fabricate a human-operated ADE session or EDA results"
        )

    def run_ade(self, task: TaskSpec) -> AdapterResult:
        raise RuntimeError(
            "ade.run requires the real Bridge; the demo adapter cannot fabricate "
            "a Maestro history or EDA results"
        )

    def apply_ade_variables(self, task: TaskSpec) -> AdapterResult:
        raise RuntimeError(
            "ade.variables.apply requires the real Bridge; the demo adapter cannot "
            "write or verify a persistent Maestro setup"
        )

    def apply_ade_corners(self, task: TaskSpec) -> AdapterResult:
        raise RuntimeError(
            "ade.corners.apply requires the real Bridge; the demo adapter cannot "
            "write or verify persistent Maestro corners"
        )

    def apply_ade_setup(self, task: TaskSpec) -> AdapterResult:
        raise RuntimeError(
            "ade.setup.apply requires the real Bridge; the demo adapter cannot "
            "write or verify persistent Maestro analyses/outputs"
        )

    def simulate(
        self, task: TaskSpec, parameters: dict[str, float]
    ) -> AdapterResult:
        if task.operating_conditions:
            base_task = task.model_copy(update={"operating_conditions": []})
            rows: list[dict[str, Any]] = []
            common_parameters: dict[str, float] | None = None
            issues: list[str] = []
            warnings: list[str] = []
            for condition in task.operating_conditions:
                condition_parameters = dict(parameters)
                if condition.vdd_v is not None:
                    condition_parameters["vdd_v"] = condition.vdd_v
                result = self.simulate(base_task, condition_parameters).data
                effective = {
                    str(name): float(value)
                    for name, value in result.get("parameters", {}).items()
                }
                if common_parameters is None:
                    common_parameters = dict(effective)
                else:
                    for name in list(common_parameters):
                        if name not in effective or not math.isclose(
                            common_parameters[name],
                            effective[name],
                            rel_tol=1e-9,
                            abs_tol=1e-12,
                        ):
                            common_parameters.pop(name)
                issues.extend(
                    f"{condition.name}: {value}"
                    for value in result.get("analysis_issues", [])
                )
                warnings.extend(
                    f"{condition.name}: {value}"
                    for value in result.get("analysis_warnings", [])
                )
                rows.append(
                    {
                        "condition": condition.model_dump(mode="json"),
                        "result": result,
                    }
                )
            return AdapterResult(
                data={
                    "parameters": common_parameters or dict(parameters),
                    "metrics": {},
                    "metric_sources": {},
                    "analysis_complete": all(
                        bool(row["result"].get("analysis_complete", True))
                        for row in rows
                    ),
                    "analysis_issues": issues,
                    "analysis_warnings": warnings,
                    "operating_condition_results": rows,
                    "warning": "analytical demo only; not an EDA result",
                },
                evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
            )
        if task.resolved_analysis() is AnalysisKind.QUALITY:
            results = {
                analysis.value: self.simulate(
                    task.model_copy(update={"analysis": analysis}), parameters
                ).data
                for analysis in task.resolved_analyses()
            }
            data = merge_analysis_bundle(results)
            data["warning"] = "analytical demo only; not an EDA result"
            return AdapterResult(
                data=data,
                evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
            )
        schematic = self._schematics.get(self._key(task))
        if schematic is None:
            raise RuntimeError("demo schematic does not exist")
        effective_parameters = dict(parameters)
        effective_parameters.update(schematic["semantic_parameters"])
        if task.circuit is CircuitKind.DIFFERENTIAL_PAIR:
            width_um = effective_parameters["input_width_um"]
            length_um = effective_parameters["length_um"]
            resistance = effective_parameters["load_resistance_ohm"]
            real_tail = schematic.get("topology_variant") == (
                "resistive_load_nmos_differential_pair_with_tail_device"
            )
            common_mode_v = effective_parameters.get("common_mode_v", 0.45)
            vdd_v = effective_parameters.get("vdd_v", 0.9)
            beta_a_per_v2 = 200e-6 * width_um / length_um
            tail_output_resistance: float | None
            ideal_tail_current_a: float | None
            tail_overdrive_v: float | None = None
            tail_gm_s: float | None = None
            tail_gds_s: float | None = None
            if real_tail:
                if "tail_bias_v" not in effective_parameters:
                    raise RuntimeError("real-tail differential pair requires tail_bias_v")
                if {
                    "tail_current_ua",
                    "tail_output_resistance_ohm",
                } & effective_parameters.keys():
                    raise RuntimeError(
                        "real-tail differential pair rejects ideal-tail parameters"
                    )
                tail_width_um = effective_parameters["tail_width_um"]
                tail_length_um = effective_parameters["tail_length_um"]
                tail_overdrive_v = max(
                    float(effective_parameters["tail_bias_v"]) - 0.25,
                    0.0,
                )
                tail_beta_a_per_v2 = (
                    200e-6 * tail_width_um / tail_length_um
                )
                total_tail_current_a = (
                    0.5 * tail_beta_a_per_v2 * tail_overdrive_v**2
                )
                tail_gm_s = max(
                    tail_beta_a_per_v2 * tail_overdrive_v,
                    1e-12,
                )
                tail_gds_s = max(tail_gm_s / 20.0, 1e-9)
                tail_output_resistance = 1.0 / tail_gds_s
                ideal_tail_current_a = None
            else:
                if "tail_bias_v" in effective_parameters:
                    raise RuntimeError(
                        "ideal-tail differential pair rejects tail_bias_v"
                    )
                ideal_tail_current_a = (
                    effective_parameters.get("tail_current_ua", 50.0) * 1e-6
                )
                tail_output_resistance = effective_parameters.get(
                    "tail_output_resistance_ohm"
                )
                total_tail_current_a = ideal_tail_current_a
            if not real_tail and tail_output_resistance is not None:
                for _ in range(20):
                    trial_overdrive_v = math.sqrt(
                        max(total_tail_current_a / beta_a_per_v2, 1e-12)
                    )
                    trial_tail_v = common_mode_v - 0.25 - trial_overdrive_v
                    total_tail_current_a = ideal_tail_current_a + (
                        trial_tail_v / tail_output_resistance
                    )
            branch_current_a = 0.5 * total_tail_current_a
            overdrive_v = math.sqrt(
                max(2.0 * branch_current_a / beta_a_per_v2, 1e-12)
            )
            tail_v = common_mode_v - 0.25 - overdrive_v
            output_v = vdd_v - branch_current_a * resistance
            gm_s = 2.0 * branch_current_a / overdrive_v
            gds_s = max(gm_s / 20.0, 1e-9)
            metrics = extract_differential_pair_dc_metrics(
                vdd_v=vdd_v,
                common_mode_v=common_mode_v,
                outp_v=output_v,
                outn_v=output_v,
                tail_v=tail_v,
                branch_p_current_a=branch_current_a,
                branch_n_current_a=branch_current_a,
                tail_source_current_a=total_tail_current_a,
                supply_source_current_a=-total_tail_current_a,
                branch_p_vdsat_v=overdrive_v,
                branch_n_vdsat_v=overdrive_v,
                branch_p_gm_s=gm_s,
                branch_n_gm_s=gm_s,
                branch_p_gds_s=gds_s,
                branch_n_gds_s=gds_s,
                load_resistance_ohm=resistance,
            )
            if real_tail:
                assert tail_overdrive_v is not None
                assert tail_gm_s is not None
                assert tail_gds_s is not None
                tail_saturation_margin_v = tail_v - tail_overdrive_v
                metrics.update(
                    {
                        "tail_device_current_ua": total_tail_current_a * 1e6,
                        "tail_device_vgs_v": float(effective_parameters["tail_bias_v"]),
                        "tail_device_vds_v": tail_v,
                        "tail_device_vdsat_v": tail_overdrive_v,
                        "tail_device_gm_us": tail_gm_s * 1e6,
                        "tail_device_gds_us": tail_gds_s * 1e6,
                        "tail_device_saturation_margin_v": tail_saturation_margin_v,
                        "tail_device_saturation_region": (
                            1.0 if tail_saturation_margin_v >= 0.0 else 0.0
                        ),
                        "tail_device_branch_sum_mismatch_percent": 0.0,
                    }
                )
            else:
                assert ideal_tail_current_a is not None
                metrics["ideal_tail_source_current_ua"] = (
                    ideal_tail_current_a * 1e6
                )
            if not real_tail and tail_output_resistance is not None:
                metrics.update(
                    {
                        "tail_output_resistance_ohm": tail_output_resistance,
                        "tail_output_resistor_current_ua": abs(
                            total_tail_current_a - ideal_tail_current_a
                        )
                        * 1e6,
                    }
                )
            analysis_complete = True
            analysis_issues: list[str] = []
            analysis_warnings = [
                "analytical differential-pair demo; not an EDA result"
            ]
            if real_tail and metrics["tail_device_saturation_region"] != 1.0:
                analysis_warnings.append(
                    "operating-point constraint: MNTAIL is not in saturation; "
                    "use tail_device_saturation_region to evaluate feasibility"
                )
            output_resistance = 1.0 / (1.0 / resistance + gds_s)
            low_frequency_gain = gm_s * output_resistance
            noise_diagnostics: dict[str, object] = {}
            if task.resolved_analysis() is AnalysisKind.AC:
                if task.ac_sweep is None:
                    raise RuntimeError("demo differential AC analysis requires ac_sweep")
                sweep = task.ac_sweep
                decades = math.log10(sweep.stop_hz / sweep.start_hz)
                steps = math.ceil(decades * sweep.points_per_decade)
                frequency_hz = [
                    sweep.start_hz * 10.0 ** (index / sweep.points_per_decade)
                    for index in range(steps + 1)
                    if sweep.start_hz
                    * 10.0 ** (index / sweep.points_per_decade)
                    <= sweep.stop_hz
                ]
                if not math.isclose(frequency_hz[-1], sweep.stop_hz, rel_tol=1e-12):
                    frequency_hz.append(sweep.stop_hz)
                capacitance_f = (2.0 + 0.2 * width_um) * 1e-15
                pole_hz = 1.0 / (
                    2.0 * math.pi * output_resistance * capacitance_f
                )
                transfer = [
                    -low_frequency_gain / (1.0 + 1j * frequency / pole_hz)
                    for frequency in frequency_hz
                ]
                ac_metrics, diagnostics = extract_differential_pair_ac_metrics(
                    frequency_hz,
                    [0.5 + 0.0j] * len(frequency_hz),
                    [-0.5 + 0.0j] * len(frequency_hz),
                    [0.5 * value for value in transfer],
                    [-0.5 * value for value in transfer],
                    reference_points=sweep.reference_points,
                    max_reference_variation_db=sweep.max_reference_variation_db,
                )
                metrics.update(ac_metrics)
                analysis_issues.extend(
                    str(value) for value in diagnostics.get("issues", [])
                )
                analysis_warnings.extend(
                    str(value) for value in diagnostics.get("warnings", [])
                )
                if metrics["both_saturation_region"] != 1.0:
                    analysis_warnings.append(
                        "operating-point constraint: differential AC branches are "
                        "not both in saturation; use both_saturation_region to "
                        "evaluate feasibility"
                    )
                analysis_complete = (
                    bool(diagnostics.get("analysis_complete", False))
                    and not analysis_issues
                )
                if tail_output_resistance is not None:
                    common_mode_gain = (
                        gm_s
                        * output_resistance
                        / (1.0 + 2.0 * gm_s * tail_output_resistance)
                    )
                    common_mode_transfer = [
                        -common_mode_gain
                        / (1.0 + 1j * frequency / (10.0 * pole_hz))
                        for frequency in frequency_hz
                    ]
                    (
                        common_mode_metrics,
                        common_mode_diagnostics,
                    ) = extract_differential_pair_common_mode_ac_metrics(
                        frequency_hz,
                        [1.0 + 0.0j] * len(frequency_hz),
                        [1.0 + 0.0j] * len(frequency_hz),
                        common_mode_transfer,
                        common_mode_transfer,
                        reference_points=sweep.reference_points,
                        max_reference_variation_db=(
                            sweep.max_reference_variation_db
                        ),
                    )
                    metrics.update(common_mode_metrics)
                    cmrr_metrics, cmrr_diagnostics = (
                        extract_differential_pair_cmrr_response_metrics(
                            frequency_hz,
                            [0.5 + 0.0j] * len(frequency_hz),
                            [-0.5 + 0.0j] * len(frequency_hz),
                            [0.5 * value for value in transfer],
                            [-0.5 * value for value in transfer],
                            frequency_hz,
                            [1.0 + 0.0j] * len(frequency_hz),
                            [1.0 + 0.0j] * len(frequency_hz),
                            common_mode_transfer,
                            common_mode_transfer,
                            reference_points=sweep.reference_points,
                            max_reference_variation_db=(
                                sweep.max_reference_variation_db
                            ),
                        )
                    )
                    metrics.update(cmrr_metrics)
                    common_mode_issues = [
                        str(value)
                        for value in common_mode_diagnostics.get("issues", [])
                    ]
                    common_mode_reference = common_mode_diagnostics.get(
                        "reference", {}
                    )
                    common_mode_reference_complete = (
                        isinstance(common_mode_reference, dict)
                        and common_mode_reference.get("status") == "flat"
                    )
                    if not common_mode_reference_complete:
                        analysis_issues.append(
                            "common-mode AC low-frequency reference is not flat"
                        )
                    analysis_warnings.extend(
                        "common-mode standalone bandwidth not used for CMRR "
                        f"acceptance: {value}"
                        for value in common_mode_issues
                    )
                    analysis_warnings.extend(
                        f"common-mode AC: {value}"
                        for value in common_mode_diagnostics.get("warnings", [])
                    )
                    analysis_issues.extend(
                        f"CMRR response: {value}"
                        for value in cmrr_diagnostics.get("issues", [])
                    )
                    analysis_warnings.extend(
                        f"CMRR response: {value}"
                        for value in cmrr_diagnostics.get("warnings", [])
                    )
                    analysis_complete = (
                        analysis_complete
                        and common_mode_reference_complete
                        and bool(cmrr_diagnostics.get("analysis_complete", False))
                        and not analysis_issues
                    )
            elif task.resolved_analysis() is AnalysisKind.TRANSIENT:
                if task.linearity_sweep is None:
                    raise RuntimeError(
                        "demo differential transient analysis requires linearity_sweep"
                    )
                amplitudes = task.linearity_sweep.amplitudes_v
                compression_scale_v = max(0.12, amplitudes[0] * 2.0)
                point_metrics: list[dict[str, float]] = []
                for amplitude in amplitudes:
                    normalized = amplitude / compression_scale_v
                    gain = low_frequency_gain / math.sqrt(
                        1.0 + normalized**4
                    )
                    thd_percent = 1.5 * normalized**2
                    power_uw = metrics["dc_supply_power_uw"] * (
                        1.0 + 0.04 * normalized**2
                    )
                    point_metrics.append(
                        {
                            "large_signal_gain_v_per_v": gain,
                            "output_fundamental_v_peak": amplitude * gain,
                            "thd_percent": thd_percent,
                            "average_supply_power_uw": power_uw,
                            "output_peak_to_peak_v": 2.0 * amplitude * gain,
                            "hd2_dbc": 20.0
                            * math.log10(max(thd_percent / 500.0, 1e-15)),
                            "hd3_dbc": 20.0
                            * math.log10(max(thd_percent / 100.0, 1e-15)),
                        }
                    )
                linearity_metrics, linearity_diagnostics = (
                    aggregate_differential_pair_linearity_metrics(
                        amplitudes,
                        point_metrics,
                        compression_db=task.linearity_sweep.compression_db,
                    )
                )
                metrics.update(linearity_metrics)
                analysis_warnings.extend(
                    str(value)
                    for value in linearity_diagnostics.get("warnings", [])
                )
                if metrics["both_saturation_region"] != 1.0:
                    analysis_warnings.append(
                        "operating-point constraint: differential transient branches "
                        "are not both in saturation; use both_saturation_region to "
                        "evaluate feasibility"
                    )
                analysis_complete = not analysis_issues
            elif task.resolved_analysis() is AnalysisKind.NOISE:
                if task.noise_sweep is None:
                    raise RuntimeError(
                        "demo differential noise analysis requires noise_sweep"
                    )
                sweep = task.noise_sweep
                decades = math.log10(sweep.stop_hz / sweep.start_hz)
                steps = math.ceil(decades * sweep.points_per_decade)
                frequency_hz = [
                    sweep.start_hz * 10.0 ** (index / sweep.points_per_decade)
                    for index in range(steps + 1)
                    if sweep.start_hz
                    * 10.0 ** (index / sweep.points_per_decade)
                    <= sweep.stop_hz
                ]
                if not math.isclose(
                    frequency_hz[-1], sweep.stop_hz, rel_tol=1e-12
                ):
                    frequency_hz.append(sweep.stop_hz)
                boltzmann = 1.380649e-23
                input_density = math.sqrt(
                    4.0 * boltzmann * 300.0 * (4.0 / 3.0) / max(gm_s, 1e-12)
                )
                raw_noise_metrics, noise_diagnostics = (
                    extract_common_source_noise_metrics(
                        frequency_hz,
                        [input_density * low_frequency_gain] * len(frequency_hz),
                        [input_density] * len(frequency_hz),
                    )
                )
                metrics.update(
                    {
                        f"differential_{name}": value
                        for name, value in raw_noise_metrics.items()
                    }
                )
                if metrics["both_saturation_region"] != 1.0:
                    analysis_warnings.append(
                        "operating-point constraint: differential noise branches are "
                        "not both in saturation; use both_saturation_region to "
                        "evaluate feasibility"
                    )
                analysis_complete = not analysis_issues
            return AdapterResult(
                data={
                    "parameters": effective_parameters,
                    "metrics": metrics,
                    "metric_sources": {
                        name: "software_inference" for name in metrics
                    },
                    "analysis_complete": analysis_complete,
                    "analysis_issues": analysis_issues,
                    "analysis_warnings": analysis_warnings,
                    "noise_diagnostics": noise_diagnostics,
                    "warning": "analytical demo only; not an EDA result",
                },
                evidence_source=EvidenceSource.SOFTWARE_INFERENCE,
            )
        if task.circuit is CircuitKind.COMMON_SOURCE:
            width = effective_parameters["device_width_um"]
            length = effective_parameters["length_um"]
            resistance = effective_parameters["load_resistance_ohm"]
            bias = effective_parameters.get("bias_v", 0.45)
            vdd = effective_parameters.get("vdd_v", 0.9)
            overdrive = max(bias - 0.25, 0.0)
            transconductance_factor = 100.0 * width * (0.03 / length) * 1e-6
            source_resistance = effective_parameters.get(
                "source_resistance_ohm", 0.0
            )
            drain_current_a = (
                transconductance_factor
                * overdrive
                / (1.0 + transconductance_factor * source_resistance)
            )
            source_v = drain_current_a * source_resistance
            vout = vdd - drain_current_a * resistance
            effective_overdrive = max(overdrive - source_v, 0.0)
            gm_s = 2.0 * drain_current_a / max(effective_overdrive, 0.01)
            gds_s = max(gm_s / 20.0, 1e-9)
            metrics = extract_common_source_dc_metrics(
                vdd_v=vdd,
                vin_v=bias,
                vout_v=vout,
                vss_v=source_v,
                drain_current_a=drain_current_a,
                vdsat_v=overdrive,
                gm_s=gm_s,
                gds_s=gds_s,
                load_resistance_ohm=resistance,
            )
            metrics.update(
                {
                    "supply_current_ua": drain_current_a * 1e6,
                    "dc_supply_power_uw": drain_current_a * vdd * 1e6,
                }
            )
            if source_resistance > 0.0:
                metrics.update(
                    {
                        "source_voltage_v": source_v,
                        "source_degeneration_drop_v": source_v,
                        "source_resistor_current_ua": (
                            source_v / source_resistance * 1e6
                        ),
                        "source_current_mismatch_percent": 0.0,
                    }
                )
            analysis_complete = True
            analysis_issues: list[str] = []
            analysis_warnings: list[str] = []
            ac_diagnostics: dict[str, object] | None = None
            linearity_diagnostics: dict[str, object] | None = None
            noise_diagnostics: dict[str, object] | None = None
            output_resistance = 1.0 / (1.0 / resistance + gds_s)
            effective_gm = gm_s / (1.0 + gm_s * source_resistance)
            low_frequency_gain = effective_gm * output_resistance
            if task.resolved_analysis() is AnalysisKind.AC:
                if task.ac_sweep is None:
                    raise RuntimeError("demo AC analysis requires ac_sweep")
                sweep = task.ac_sweep
                decades = math.log10(sweep.stop_hz / sweep.start_hz)
                steps = math.ceil(decades * sweep.points_per_decade)
                frequency_hz = [
                    sweep.start_hz * 10.0 ** (index / sweep.points_per_decade)
                    for index in range(steps + 1)
                    if sweep.start_hz
                    * 10.0 ** (index / sweep.points_per_decade)
                    <= sweep.stop_hz
                ]
                if not math.isclose(frequency_hz[-1], sweep.stop_hz, rel_tol=1e-12):
                    frequency_hz.append(sweep.stop_hz)
                capacitance_f = (
                    2.0 + 0.2 * width + effective_parameters.get("load_ff", 0.0)
                ) * 1e-15
                pole_hz = 1.0 / (
                    2.0 * math.pi * output_resistance * capacitance_f
                )
                transfer = [
                    -low_frequency_gain / (1.0 + 1j * frequency / pole_hz)
                    for frequency in frequency_hz
                ]
                ac_metrics, ac_diagnostics = extract_common_source_ac_metrics(
                    frequency_hz,
                    [1.0 + 0.0j] * len(frequency_hz),
                    transfer,
                    reference_points=sweep.reference_points,
                    max_reference_variation_db=sweep.max_reference_variation_db,
                )
                metrics.update(ac_metrics)
                analysis_issues.extend(
                    str(value) for value in ac_diagnostics.get("issues", [])
                )
                analysis_warnings.extend(
                    str(value) for value in ac_diagnostics.get("warnings", [])
                )
                if metrics["saturation_region"] != 1.0:
                    analysis_issues.append(
                        "AC design metrics require a saturated DC operating point"
                    )
                analysis_complete = (
                    bool(ac_diagnostics.get("analysis_complete", False))
                    and not analysis_issues
                )
            elif task.resolved_analysis() is AnalysisKind.TRANSIENT:
                if task.linearity_sweep is None:
                    raise RuntimeError(
                        "demo transient analysis requires linearity_sweep"
                    )
                amplitudes = task.linearity_sweep.amplitudes_v
                compression_scale_v = max(
                    min(bias, vdd - bias, 0.2), amplitudes[0] * 2.0
                )
                point_metrics: list[dict[str, float]] = []
                for amplitude in amplitudes:
                    normalized = amplitude / compression_scale_v
                    gain = low_frequency_gain / math.sqrt(1.0 + normalized**4)
                    thd_percent = 2.0 * normalized**2
                    power_uw = metrics["dc_supply_power_uw"] * (
                        1.0 + 0.05 * normalized**2
                    )
                    point_metrics.append(
                        {
                            "large_signal_gain_v_per_v": gain,
                            "output_fundamental_v_peak": amplitude * gain,
                            "thd_percent": thd_percent,
                            "average_supply_power_uw": power_uw,
                            "output_peak_to_peak_v": 2.0 * amplitude * gain,
                            "hd2_dbc": 20.0
                            * math.log10(max(thd_percent / 100.0, 1e-15)),
                            "hd3_dbc": 20.0
                            * math.log10(max(thd_percent / 200.0, 1e-15)),
                        }
                    )
                linearity_metrics, linearity_diagnostics = (
                    aggregate_common_source_linearity_metrics(
                        amplitudes,
                        point_metrics,
                        compression_db=task.linearity_sweep.compression_db,
                    )
                )
                metrics.update(linearity_metrics)
                analysis_warnings.extend(
                    str(value)
                    for value in linearity_diagnostics.get("warnings", [])
                )
                if metrics["saturation_region"] != 1.0:
                    analysis_issues.append(
                        "linearity metrics require a saturated DC operating point"
                    )
                analysis_complete = not analysis_issues
            elif task.resolved_analysis() is AnalysisKind.NOISE:
                if task.noise_sweep is None:
                    raise RuntimeError("demo noise analysis requires noise_sweep")
                sweep = task.noise_sweep
                decades = math.log10(sweep.stop_hz / sweep.start_hz)
                steps = math.ceil(decades * sweep.points_per_decade)
                frequency_hz = [
                    sweep.start_hz * 10.0 ** (index / sweep.points_per_decade)
                    for index in range(steps + 1)
                    if sweep.start_hz
                    * 10.0 ** (index / sweep.points_per_decade)
                    <= sweep.stop_hz
                ]
                if not math.isclose(frequency_hz[-1], sweep.stop_hz, rel_tol=1e-12):
                    frequency_hz.append(sweep.stop_hz)
                boltzmann = 1.380649e-23
                input_density = math.sqrt(
                    4.0 * boltzmann * 300.0 * (2.0 / 3.0) / max(gm_s, 1e-12)
                )
                noise_metrics, noise_diagnostics = extract_common_source_noise_metrics(
                    frequency_hz,
                    [input_density * low_frequency_gain] * len(frequency_hz),
                    [input_density] * len(frequency_hz),
                )
                metrics.update(noise_metrics)
                if metrics["saturation_region"] != 1.0:
                    analysis_issues.append(
                        "noise metrics require a saturated DC operating point"
                    )
                analysis_complete = not analysis_issues
            return AdapterResult(
                data={
                    "parameters": effective_parameters,
                    "metrics": metrics,
                    "metric_sources": {
                        name: EvidenceSource.SOFTWARE_INFERENCE.value
                        for name in metrics
                    },
                    "analysis_complete": analysis_complete,
                    "analysis_issues": analysis_issues,
                    "analysis_warnings": analysis_warnings,
                    "ac_diagnostics": ac_diagnostics,
                    "linearity_diagnostics": linearity_diagnostics,
                    "noise_diagnostics": noise_diagnostics,
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
