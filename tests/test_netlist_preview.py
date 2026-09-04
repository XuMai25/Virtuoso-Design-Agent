from __future__ import annotations

import copy
import math
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from pydantic import ValidationError

from virtuoso_design_agent.adapters.base import AdapterResult
from virtuoso_design_agent.adapters.bridge_worker import (
    _parse_spectre_environment_probe,
    _preview_ac_metrics,
    _preview_comparisons,
    _preview_dc_metrics,
    simulate_netlist_preview,
)
from virtuoso_design_agent.adapters.demo import DeterministicDemoAdapter
from virtuoso_design_agent.adapters.subprocess_bridge import SubprocessBridgeAdapter
from virtuoso_design_agent.executor import TaskExecutor
from virtuoso_design_agent.models import EvidenceSource, RunStatus, TaskSpec
from virtuoso_design_agent.netlist_preview import (
    NetlistPreviewSpec,
    render_spectre_preview_deck,
)
from virtuoso_design_agent.planner import build_plan
from virtuoso_design_agent.profiles import load_pdk_profile


def _preview_spec_data() -> dict:
    return {
        "id": "common-source-cascode-ab",
        "input_source": "VIN_SRC",
        "supply_sources": ["VDD_SRC"],
        "voltage_sources": [
            {
                "name": "VDD_SRC",
                "positive": "VDD",
                "negative": "0",
                "dc_v": 0.9,
            },
            {
                "name": "VIN_SRC",
                "positive": "IN",
                "negative": "0",
                "dc_v": 0.35,
                "ac_magnitude_v": 1.0,
            },
            {
                "name": "VCAS_SRC",
                "positive": "VCAS",
                "negative": "0",
                "dc_v": 0.545,
            },
        ],
        "resistors": [
            {
                "name": "RLOAD",
                "positive": "VDD",
                "negative": "OUT",
                "resistance_ohm": 20_000.0,
            }
        ],
        "capacitors": [
            {
                "name": "CLOAD",
                "positive": "OUT",
                "negative": "0",
                "capacitance_f": 2e-15,
            }
        ],
        "variants": [
            {
                "id": "common_source",
                "output_positive": "OUT",
                "mosfets": [
                    {
                        "name": "MN0",
                        "polarity": "nmos",
                        "drain": "OUT",
                        "gate": "IN",
                        "source": "0",
                        "bulk": "0",
                        "width_um": 1.0,
                        "length_um": 0.03,
                    }
                ],
            },
            {
                "id": "cascode_common_source",
                "output_positive": "OUT",
                "mosfets": [
                    {
                        "name": "MN0",
                        "polarity": "nmos",
                        "drain": "NCAS",
                        "gate": "IN",
                        "source": "0",
                        "bulk": "0",
                        "width_um": 1.0,
                        "length_um": 0.03,
                    },
                    {
                        "name": "MNCAS",
                        "polarity": "nmos",
                        "drain": "OUT",
                        "gate": "VCAS",
                        "source": "NCAS",
                        "bulk": "0",
                        "width_um": 0.75,
                        "length_um": 0.03,
                    },
                ],
            },
        ],
    }


def _preview_task_data() -> dict:
    return {
        "id": "common-source-cascode-preview",
        "operation": "simulation.run",
        "circuit": "netlist_preview",
        "analysis": "ac",
        "ac_sweep": {
            "start_hz": 1e4,
            "stop_hz": 1e12,
            "points_per_decade": 30,
        },
        "netlist_preview": _preview_spec_data(),
        "constraints": [
            {
                "metric": "common_source__all_mos_saturation_region",
                "relation": ">=",
                "value": 1.0,
            }
        ],
        "safety": {"allow_remote_compute": True},
    }


def test_spectre_environment_probe_requires_one_exact_executable_path() -> None:
    assert _parse_spectre_environment_probe(
        "/tools/Cadence/SPECTRE211/bin/spectre\n"
    ) == "/tools/Cadence/SPECTRE211/bin/spectre"

    with pytest.raises(RuntimeError, match="did not return one tool path"):
        _parse_spectre_environment_probe("spectre\n")


def test_preview_renders_same_declared_conditions_without_oa_or_si() -> None:
    spec = NetlistPreviewSpec.model_validate(_preview_spec_data())
    profile = load_pdk_profile("nics4304_tsmc28").model_dump(mode="json")

    baseline = render_spectre_preview_deck(
        spec,
        "common_source",
        profile,
        analysis="ac",
        ac_sweep={"start_hz": 1e4, "stop_hz": 1e12},
    )
    cascode = render_spectre_preview_deck(
        spec,
        "cascode_common_source",
        profile,
        analysis="ac",
        ac_sweep={"start_hz": 1e4, "stop_hz": 1e12},
    )

    for shared in (
        "VDD_SRC (VDD 0) vsource dc=0.9",
        "VIN_SRC (IN 0) vsource dc=0.35 mag=1 type=dc",
        "RLOAD (VDD OUT) resistor r=20000",
        "CLOAD (OUT 0) capacitor c=2e-15",
    ):
        assert shared in baseline
        assert shared in cascode
    assert "MN0 (OUT IN 0 0)" in baseline
    assert "MNCAS" not in baseline
    assert "MN0 (NCAS IN 0 0)" in cascode
    assert "MNCAS (OUT VCAS NCAS 0)" in cascode
    assert "si -batch" not in baseline
    assert "maestro" not in baseline.lower()
    assert spec.canonical_sha256() == NetlistPreviewSpec.model_validate(
        spec.model_dump(mode="json")
    ).canonical_sha256()


def test_preview_contract_rejects_raw_code_and_ambiguous_graphs() -> None:
    raw_code = _preview_spec_data()
    raw_code["variants"][0]["mosfets"][0]["model_parameters"] = {
        "delvto": "0; system('unsafe')"
    }
    with pytest.raises(ValidationError, match="numeric Spectre literal"):
        NetlistPreviewSpec.model_validate(raw_code)

    duplicate = _preview_spec_data()
    duplicate["variants"][0]["resistors"] = [
        {
            "name": "RLOAD",
            "positive": "OUT",
            "negative": "0",
            "resistance_ohm": 1_000.0,
        }
    ]
    with pytest.raises(ValidationError, match="repeats shared element names"):
        NetlistPreviewSpec.model_validate(duplicate)

    disconnected = _preview_spec_data()
    disconnected["variants"][0]["output_positive"] = "UNCONNECTED"
    with pytest.raises(ValidationError, match="is not connected"):
        NetlistPreviewSpec.model_validate(disconnected)


def test_preview_task_is_target_free_compute_only_and_has_explicit_plan() -> None:
    task = TaskSpec.model_validate(_preview_task_data())
    plan = build_plan(task)

    assert task.target is None
    assert plan.requires_remote_compute
    assert not plan.requires_remote_write
    assert [step.capability for step in plan.steps] == [
        "bridge.spectre.probe",
        "netlist.preview.render",
        "simulation.preview.run",
        "simulation.preview.compare",
        "evidence.persist",
    ]
    assert all("OA" not in step.capability for step in plan.steps)


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"target": {"library": "vda_test", "cell": "vda_preview"}}, "OA target"),
        ({"safety": {"allow_remote_write": True}}, "cannot request remote OA writes"),
        ({"analysis": "transient", "ac_sweep": None}, "explicit dc or ac"),
        ({"parameters": {"device_width_um": 1.0}}, "accepts only"),
    ],
)
def test_preview_task_rejects_oa_or_unstructured_side_channels(
    update: dict,
    message: str,
) -> None:
    data = _preview_task_data()
    data.update(update)
    with pytest.raises(ValidationError, match=message):
        TaskSpec.model_validate(data)


def test_preview_dc_extraction_and_comparison_preserve_sources() -> None:
    spec_data = _preview_spec_data()
    spec_data["variants"] = [spec_data["variants"][0]]
    spec = NetlistPreviewSpec.model_validate(spec_data)
    variant = spec.variants[0]
    data = {
        "dc_VDD": 0.9,
        "dc_IN": 0.35,
        "dc_VCAS": 0.545,
        "dc_OUT": 0.62,
        "dc_VDD_SRC:p": -14e-6,
        "dc_VIN_SRC:p": 0.0,
        "dc_VCAS_SRC:p": 0.0,
        "dcOpInfo_MN0:ids": 14e-6,
        "dcOpInfo_MN0:vgs": 0.35,
        "dcOpInfo_MN0:vds": 0.62,
        "dcOpInfo_MN0:vbs": 0.0,
        "dcOpInfo_MN0:vdsat": 0.2,
        "dcOpInfo_MN0:gm": 150e-6,
        "dcOpInfo_MN0:gds": 5e-6,
        "dcOpInfo_MN0:gmb": 20e-6,
    }

    metrics, evidence = _preview_dc_metrics(spec, variant, data)
    flat, sources, comparison = _preview_comparisons(
        spec,
        {"common_source": metrics},
    )

    assert metrics["output_dc_v"] == pytest.approx(0.62)
    assert metrics["dc_supply_power_uw"] == pytest.approx(12.6)
    assert metrics["all_mos_saturation_region"] == 1.0
    assert evidence["source_voltage_consistency"] == "matched"
    assert flat["common_source__MN0__gm_us"] == pytest.approx(150.0)
    assert sources["common_source__MN0__gm_us"] == "eda_result"
    assert sources["common_source__gate_area_proxy_um2"] == "software_inference"
    assert comparison["baseline_variant"] == "common_source"


def test_preview_ac_extraction_rejects_an_empty_waveform() -> None:
    spec = NetlistPreviewSpec.model_validate(_preview_spec_data())
    with pytest.raises(RuntimeError, match="ac_freq is empty"):
        _preview_ac_metrics(
            spec,
            spec.variants[0],
            {"ac_freq": []},
            {"reference_points": 5},
        )


def test_preview_executor_never_inspects_schematic() -> None:
    class PreviewAdapter(DeterministicDemoAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.probe_count = 0
            self.simulate_count = 0

        def probe_simulator(self, pdk_profile: str) -> AdapterResult:
            self.probe_count += 1
            return AdapterResult(
                data={"profile": pdk_profile, "oa_access_performed": False},
                evidence_source=EvidenceSource.BRIDGE_READBACK,
            )

        def inspect_schematic(self, task: TaskSpec) -> AdapterResult:
            raise AssertionError("netlist preview must not inspect OA")

        def simulate(
            self,
            task: TaskSpec,
            parameters: dict[str, float],
        ) -> AdapterResult:
            self.simulate_count += 1
            return AdapterResult(
                data={
                    "parameters": {},
                    "metrics": {
                        "common_source__all_mos_saturation_region": 1.0,
                        "common_source__gain_db": 10.0,
                    },
                    "metric_sources": {
                        "common_source__all_mos_saturation_region": (
                            "software_inference"
                        ),
                        "common_source__gain_db": "eda_result",
                    },
                    "analysis_complete": True,
                },
                evidence_source=EvidenceSource.EDA_RESULT,
            )

    task = TaskSpec.model_validate(_preview_task_data())
    plan = build_plan(task)
    adapter = PreviewAdapter()
    record = TaskExecutor(adapter).execute(
        task,
        plan,
        token=plan.confirmation_token,
    )

    assert record.status is RunStatus.SUCCEEDED
    assert adapter.probe_count == 1
    assert adapter.simulate_count == 1
    assert "schematic.inspect.before" not in [action.action for action in record.actions]
    assert any("no OA, si, Maestro" in note for note in record.notes)


def test_subprocess_adapter_routes_preview_without_an_oa_target(
    tmp_path,
    monkeypatch,
) -> None:
    task = TaskSpec.model_validate(_preview_task_data())
    adapter = SubprocessBridgeAdapter(tmp_path / "bridge-python.exe")
    observed: dict = {}

    def fake_request(action, payload, *, timeout):
        observed.update(action=action, payload=payload, timeout=timeout)
        return {"metrics": {}, "analysis_complete": True}

    monkeypatch.setattr(adapter, "_request", fake_request)
    result = adapter.simulate(task, {})

    assert observed["action"] == "simulate_netlist_preview"
    assert "target" not in observed["payload"]
    assert observed["payload"]["netlist_preview"]["variants"][1]["id"] == (
        "cascode_common_source"
    )
    assert result.evidence_source is EvidenceSource.EDA_RESULT


def test_preview_worker_runs_both_decks_and_returns_eda_bound_ab(
    monkeypatch,
) -> None:
    runner_module = ModuleType("virtuoso_bridge.spectre.runner")
    tunnel_module = ModuleType("virtuoso_bridge.transport.tunnel")
    frequency_hz = [10.0 ** (4.0 + index / 10.0) for index in range(81)]
    captured_decks: dict[str, str] = {}

    inventory_commands: list[str] = []

    class Runner:
        _persistent_shell_enabled = True

        def run_command(self, command, timeout=None):
            if command.startswith("find "):
                inventory_commands.append(command)
                remote_root = command.split()[1]
                return SimpleNamespace(
                    returncode=0,
                    stdout=(
                        f"{remote_root}/00000001/preview_common_source.scs\n"
                        f"{remote_root}/00000002/"
                        "preview_cascode_common_source.scs\n"
                    ),
                    stderr="",
                )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

    runner = Runner()

    class SSHClient:
        def __init__(self):
            self.ssh_runner = runner

        @classmethod
        def is_running(cls):
            return True

        @classmethod
        def from_env(cls, **kwargs):
            return cls()

        def close(self):
            return None

    parallel_calls: list[dict] = []

    class Simulator:
        def __init__(self, work_dir):
            self.work_dir = Path(work_dir)

        def run_parallel(self, tasks, max_workers):
            parallel_calls.append(
                {
                    "task_count": len(tasks),
                    "max_workers": max_workers,
                }
            )
            results = []
            for index, (netlist, parameters) in enumerate(tasks, start=1):
                assert parameters == {}
                deck = netlist.read_text(encoding="utf-8")
                cascode = "MNCAS" in deck
                variant = "cascode_common_source" if cascode else "common_source"
                captured_decks[variant] = deck
                gain = 20.0 if cascode else 10.0
                pole_hz = 1e9 if cascode else 1e8
                transfer = [
                    -gain / (1.0 + 1j * frequency / pole_hz)
                    for frequency in frequency_hz
                ]
                data = {
                    "dc_VDD": 0.9,
                    "dc_IN": 0.35,
                    "dc_VCAS": 0.545,
                    "dc_OUT": 0.62,
                    "dc_VDD_SRC:p": -14e-6,
                    "dc_VIN_SRC:p": 0.0,
                    "dc_VCAS_SRC:p": 0.0,
                    "dcOpInfo_MN0:ids": 14e-6,
                    "dcOpInfo_MN0:vgs": 0.35,
                    "dcOpInfo_MN0:vds": 0.3 if cascode else 0.62,
                    "dcOpInfo_MN0:vbs": 0.0,
                    "dcOpInfo_MN0:vdsat": 0.1,
                    "dcOpInfo_MN0:gm": 150e-6,
                    "dcOpInfo_MN0:gds": 5e-6,
                    "dcOpInfo_MN0:gmb": 20e-6,
                    "ac_freq": frequency_hz,
                    "ac_IN": [1.0 + 0.0j] * len(frequency_hz),
                    "ac_OUT": transfer,
                }
                if cascode:
                    data.update(
                        {
                            "dc_NCAS": 0.3,
                            "dcOpInfo_MNCAS:ids": 14e-6,
                            "dcOpInfo_MNCAS:vgs": 0.245,
                            "dcOpInfo_MNCAS:vds": 0.32,
                            "dcOpInfo_MNCAS:vbs": -0.3,
                            "dcOpInfo_MNCAS:vdsat": 0.1,
                            "dcOpInfo_MNCAS:gm": 130e-6,
                            "dcOpInfo_MNCAS:gds": 4e-6,
                            "dcOpInfo_MNCAS:gmb": 18e-6,
                        }
                    )
                task_dir = self.work_dir / f"{netlist.stem}__{index:08x}"
                raw_dir = task_dir / f"{netlist.stem}.raw"
                raw_dir.mkdir(parents=True)
                (raw_dir / "analysis.data").write_text("raw", encoding="utf-8")
                (task_dir / "spectre.out").write_text(
                    "Version test-spectre-preview\n",
                    encoding="utf-8",
                )
                results.append(
                    SimpleNamespace(
                        ok=True,
                        data=data,
                        metadata={"output_dir": str(raw_dir)},
                        tool_version="test-spectre-preview",
                        warnings=[],
                    )
                )
            return results

    runner_module.SpectreSimulator = Simulator
    tunnel_module.SSHClient = SSHClient
    monkeypatch.setitem(sys.modules, "virtuoso_bridge.spectre.runner", runner_module)
    monkeypatch.setitem(sys.modules, "virtuoso_bridge.transport.tunnel", tunnel_module)
    guard_calls: list[dict] = []

    def install_guard(client, work_dir, remote_run_dir, *, timeout):
        (Path(work_dir) / "vda_spectre_guard.sh").write_text(
            "#!/bin/sh\n",
            encoding="utf-8",
        )
        guard_calls.append(
            {
                "work_dir": Path(work_dir),
                "remote_run_dir": remote_run_dir,
                "timeout": timeout,
            }
        )
        return (
            "spectre",
            {"status": "bounded-test", "bounded_remote_process": True},
        )

    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._install_remote_spectre_guard",
        install_guard,
    )
    simulator_factory_calls: list[dict] = []

    def create_simulator(*args, **kwargs):
        simulator_factory_calls.append(kwargs)
        return Simulator(kwargs["work_dir"])

    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._create_spectre_simulator",
        create_simulator,
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._common_source_dc_data_from_result",
        lambda result: (result.data, {"selection": "test fixture"}),
    )
    monkeypatch.setattr(
        "virtuoso_design_agent.adapters.bridge_worker._spectre_ac_file_evidence_from_result",
        lambda result: {
            "selection": "test fixture",
            "ac": {
                "relative_path": "ac.ac",
                "size_bytes": 1,
                "sha256": "a" * 64,
            },
        },
    )

    preview_data = _preview_spec_data()
    preview_data["variant_source_ids"] = {
        "cascode_common_source": "cascode-seed-001"
    }
    preview_data["variant_source_ids_evidence_source"] = "software_inference"
    result = simulate_netlist_preview(
        {
            "task_id": "preview-worker",
            "profile": load_pdk_profile("nics4304_tsmc28").model_dump(mode="json"),
            "analysis": "ac",
            "analysis_source": "user_input",
            "ac_sweep": {
                "start_hz": 1e4,
                "stop_hz": 1e12,
                "points_per_decade": 10,
                "reference_points": 5,
                "max_reference_variation_db": 0.5,
            },
            "netlist_preview": preview_data,
            "timeout_seconds": 60,
        }
    )

    assert set(captured_decks) == {"common_source", "cascode_common_source"}
    assert len(guard_calls) == 1
    assert len(simulator_factory_calls) == 1
    assert parallel_calls == [{"task_count": 2, "max_workers": 2}]
    assert len(inventory_commands) == 1
    batch = result["evidence"]["batch_execution"]
    assert batch == {
        "source": "software_inference",
        "api": "SpectreSimulator.run_parallel",
        "submission_count": 2,
        "max_workers": 2,
        "result_binding": "submission_order_plus_remote_deck_inventory",
        "simulator_instance_count": 1,
        "guard_installation_count": 1,
        "remote_inventory_command_count": 1,
        "executor_lifecycle": "scoped_context_manager",
    }
    remote_root = result["evidence"]["remote_run_root"]
    remote_dirs = set()
    for variant_id, variant in result["evidence"]["variants"].items():
        assert variant["remote_run_root"] == remote_root
        assert variant["remote_deck_path"] == (
            f"{variant['remote_simulation_dir']}/preview_{variant_id}.scs"
        )
        remote_dirs.add(variant["remote_simulation_dir"])
        manifest_paths = {row["path"] for row in variant["artifact_manifest"]}
        assert f"preview_{variant_id}.scs" in manifest_paths
        assert "vda_spectre_guard.sh" in manifest_paths
    assert len(remote_dirs) == 2
    assert result["analysis_complete"] is True
    assert result["metrics"]["common_source__bandwidth_3db_hz"] == pytest.approx(
        1e8,
        rel=0.02,
    )
    assert result["metrics"][
        "cascode_common_source__bandwidth_3db_hz"
    ] == pytest.approx(1e9, rel=0.02)
    gain_ratio = result["metrics"][
        "cascode_common_source__ratio_vs__common_source__low_frequency_gain_v_per_v"
    ]
    assert gain_ratio == pytest.approx(2.0, rel=1e-3)
    assert result["metric_sources"][
        "cascode_common_source__delta_vs__common_source__bandwidth_3db_hz"
    ] == "software_inference"
    assert result["evidence"]["oa_access_performed"] is False
    assert result["evidence"]["si_netlisting_performed"] is False
    assert result["evidence"]["comparison"]["source"] == "software_inference"
    assert result["evidence"]["variant_source_ids"] == {
        "cascode_common_source": "cascode-seed-001"
    }
    assert (
        result["evidence"]["variant_source_ids_evidence_source"]
        == "software_inference"
    )
    assert all(
        item["process_lifecycle"]["bounded_remote_process"]
        for item in result["evidence"]["variants"].values()
    )
    assert math.isfinite(result["metrics"]["common_source__dc_supply_power_uw"])


def test_preview_spec_copy_does_not_share_mutable_inputs() -> None:
    data = _preview_spec_data()
    original = copy.deepcopy(data)
    NetlistPreviewSpec.model_validate(data)
    assert data == original
