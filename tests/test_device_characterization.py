from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from virtuoso_design_agent.adapters.base import AdapterInterrupted, AdapterResult
from virtuoso_design_agent.adapters.bridge_worker import (
    _mos_characterization_deck,
    _spectre_version_from_log,
)
from virtuoso_design_agent.adapters.demo import DeterministicDemoAdapter
from virtuoso_design_agent.adapters.subprocess_bridge import SubprocessBridgeAdapter
from virtuoso_design_agent.characterization import (
    enumerate_mos_characterization_points,
    normalize_mos_characterization,
)
from virtuoso_design_agent.executor import TaskExecutor
from virtuoso_design_agent.models import EvidenceSource, RunStatus, TaskSpec
from virtuoso_design_agent.planner import build_plan
from virtuoso_design_agent.profiles import load_pdk_profile
from virtuoso_design_agent.safety import SafetyViolation, authorize_execution
from virtuoso_design_agent.small_signal import (
    SmallSignalNetworkRequest,
    analyze_small_signal_network,
)


def _task(*, allow_compute: bool = True) -> TaskSpec:
    return TaskSpec.model_validate(
        {
            "id": "mos-char-test",
            "operation": "device.characterize",
            "circuit": "mos_device",
            "device_characterization": {
                "polarities": ["nmos", "pmos"],
                "width_um": 1.0,
                "model_parameters_by_polarity": {
                    "nmos": {"ad": "3.75e-14", "dfm_flag": "0"},
                    "pmos": {"ad": "4.0e-14", "dfm_flag": "0"},
                },
                "lengths_um": [0.03, 0.06],
                "vgs_magnitudes_v": [0.4, 0.6],
                "vds_magnitudes_v": [0.3, 0.6],
                "vsb_magnitudes_v": [0.0, 0.1],
                "holdout_points": [
                    {
                        "polarity": polarity,
                        "length_um": 0.045,
                        "vgs_magnitude_v": 0.5,
                        "vds_magnitude_v": 0.45,
                        "vsb_magnitude_v": 0.05,
                    }
                    for polarity in ("nmos", "pmos")
                ],
                "temperature_c": 27.0,
                "maximum_holdout_normalized_error": 0.25,
            },
            "safety": {"allow_remote_compute": allow_compute},
        }
    )


def _manifest() -> tuple[list[dict], str]:
    paths = (
        "mos_characterization.scs",
        "mos_characterization.raw/dcOp.dc",
        "mos_characterization.raw/dcOpInfo.info",
        "spectre.out",
    )
    entries = [
        {
            "path": path,
            "size_bytes": index + 10,
            "sha256": hashlib.sha256(path.encode("utf-8")).hexdigest(),
        }
        for index, path in enumerate(paths)
    ]
    canonical = json.dumps(
        sorted(entries, key=lambda item: item["path"]),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return entries, hashlib.sha256(canonical).hexdigest()


def _raw_result(task: TaskSpec) -> dict:
    assert task.device_characterization is not None
    settings = task.device_characterization
    profile = load_pdk_profile(task.pdk_profile)
    points = []
    for point in enumerate_mos_characterization_points(settings):
        sign = 1.0 if point["polarity"] == "nmos" else -1.0
        length = float(point["length_um"])
        vgs = float(point["vgs_magnitude_v"])
        vds = float(point["vds_magnitude_v"])
        vsb = float(point["vsb_magnitude_v"])
        current_density = 1e-5 + 1e-5 * length + 2e-5 * vgs + 1e-5 * vds
        current = current_density * settings.width_um
        gm_over_id = 8.0 + vgs + length
        gds_over_id = 0.1 + 0.2 * vds + length
        gmb_over_id = 0.05 + 0.1 * vsb + length
        capacitance_base = 1e-15 * (1.0 + length + vgs + vds + vsb)
        points.append(
            {
                **point,
                "model": (
                    profile.nmos_cell
                    if point["polarity"] == "nmos"
                    else profile.pmos_cell
                ),
                "instance": f"MCHAR{len(points):04d}",
                "raw": {
                    "ids_a": sign * current,
                    "vgs_v": sign * vgs,
                    "vds_v": sign * vds,
                    "vbs_v": -sign * vsb,
                    "vdsat_v": sign * (0.1 + 0.2 * vgs + length),
                    "gm_s": current * gm_over_id,
                    "gds_s": current * gds_over_id,
                    "gmb_s": current * gmb_over_id,
                    "cgs_f": capacitance_base * settings.width_um,
                    "cgd_f": capacitance_base * 0.5 * settings.width_um,
                    "cgb_f": capacitance_base * 0.25 * settings.width_um,
                    "cdb_f": capacitance_base * 0.2 * settings.width_um,
                    "csb_f": capacitance_base * 0.15 * settings.width_um,
                },
                "raw_evidence_source": "eda_result",
            }
        )
    manifest, fingerprint = _manifest()
    return {
        "task_id": task.id,
        "pdk_profile": profile.name,
        "process_corner": profile.model_section,
        "temperature_c": settings.temperature_c,
        "width_um": settings.width_um,
        "model_parameters_by_polarity": deepcopy(
            settings.model_parameters_by_polarity
        ),
        "raw_point_evidence_source": "eda_result",
        "points": points,
        "tool_version": "test-spectre",
        "warnings": [],
        "evidence": {
            "source": "eda_result",
            "remote_run_root": (
                "/data/xum/virtuoso_bridge_smoke/"
                "vda_mos_characterization_test_nonce"
            ),
            "remote_simulation_dir": (
                "/data/xum/virtuoso_bridge_smoke/"
                "vda_mos_characterization_test_nonce/deadbeef"
            ),
            "artifact_manifest": manifest,
            "manifest_sha256": fingerprint,
        },
    }


def test_device_characterization_contract_has_no_fake_oa_target() -> None:
    task = _task()
    plan = build_plan(task)

    assert task.target is None
    assert plan.requires_remote_compute
    assert not plan.requires_remote_write
    assert [step.capability for step in plan.steps] == [
        "bridge.probe",
        "device.characterize",
        "device.characterize.validate",
        "evidence.persist",
    ]
    assert "32 个训练点" in plan.steps[1].description
    assert "2 个留出点" in plan.steps[1].description


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"target": {"library": "vda_test", "cell": "vda_fake"}}, "does not accept"),
        ({"circuit": "inverter"}, "requires circuit='mos_device'"),
        ({"device_characterization": None}, "requires device_characterization"),
        ({"safety": {"allow_remote_write": True}}, "cannot request remote OA"),
    ],
)
def test_device_characterization_rejects_oa_or_ambiguous_scope(
    update: dict, message: str
) -> None:
    data = _task().model_dump(mode="json")
    data.update(update)
    with pytest.raises(ValidationError, match=message):
        TaskSpec.model_validate(data)


def test_all_other_operations_still_require_an_oa_target() -> None:
    with pytest.raises(ValidationError, match="requires an OA target"):
        TaskSpec.model_validate(
            {
                "id": "missing-target",
                "operation": "schematic.inspect",
                "circuit": "inverter",
            }
        )


def test_device_characterization_requires_compute_permission_only() -> None:
    blocked = _task(allow_compute=False)
    plan = build_plan(blocked)
    with pytest.raises(SafetyViolation, match="remote compute"):
        authorize_execution(blocked, plan, plan.confirmation_token)

    authorized = _task(allow_compute=True)
    plan = build_plan(authorized)
    authorize_execution(authorized, plan, plan.confirmation_token)


def test_subprocess_payload_omits_target_and_analysis() -> None:
    payload = SubprocessBridgeAdapter._task_payload(_task())

    assert "target" not in payload
    assert "analysis" not in payload
    assert payload["device_characterization"]["width_um"] == 1.0
    assert payload["device_characterization"]["model_parameters_by_polarity"] == {
        "nmos": {"ad": "3.75e-14", "dfm_flag": "0"},
        "pmos": {"ad": "4.0e-14", "dfm_flag": "0"},
    }


@pytest.mark.parametrize(
    ("parameters", "message"),
    [
        ({"nmos": {"w": "1u"}}, "controlled by the characterization contract"),
        ({"nmos": {"ad": "1u;alter"}}, "numeric Spectre literal"),
        ({"pmos": {"ad": "1e-14"}}, "polarity must be present"),
    ],
)
def test_characterization_model_parameters_are_bounded_and_injection_safe(
    parameters: dict, message: str
) -> None:
    data = _task().model_dump(mode="json")
    data["device_characterization"]["polarities"] = ["nmos"]
    data["device_characterization"]["model_parameters_by_polarity"] = parameters

    with pytest.raises(ValidationError, match=message):
        TaskSpec.model_validate(data)


def test_characterization_deck_has_independent_nmos_and_pmos_biases() -> None:
    task = _task()
    assert task.device_characterization is not None
    profile = load_pdk_profile(task.pdk_profile).model_dump(mode="json")
    points = enumerate_mos_characterization_points(task.device_characterization)
    deck = _mos_characterization_deck(
        profile,
        task.device_characterization,
        points,
    )

    assert f'include "{profile["model_include"]}" section=top_tt' in deck
    assert "MCHAR0000 (DP0000 GP0000 0 BP0000) nch_lvt_mac" in deck
    assert "nf=1 multi=1 ad=3.75e-14 dfm_flag=0" in deck
    pmos_index = next(
        index for index, point in enumerate(points) if point["polarity"] == "pmos"
    )
    assert f"MCHAR{pmos_index:04d}" in deck
    assert "pch_lvt_mac" in deck
    assert f"VGP{pmos_index:04d} (GP{pmos_index:04d} 0) vsource dc=-0.4" in deck
    assert f"VBP{pmos_index:04d} (BP{pmos_index:04d} 0) vsource dc=0" in deck
    assert "dcOpInfo info what=oppoint where=rawfile" in deck
    assert "MCHAR0000:gmb" in deck and "MCHAR0000:cgs" in deck


def test_characterization_extracts_spectre_version_from_hashed_log(tmp_path) -> None:
    (tmp_path / "spectre.out").write_text(
        "Cadence Spectre Circuit Simulator\nVersion 21.1.0.123.isr\n",
        encoding="utf-8",
    )

    assert _spectre_version_from_log(tmp_path) == "21.1.0.123.isr"


def test_normalization_builds_real_pdk_artifact_and_passes_linear_holdouts() -> None:
    task = _task()
    normalized = normalize_mos_characterization(task, _raw_result(task))

    artifact = normalized["artifact"]
    assert len(artifact["points"]) == 32
    assert artifact["source"] == "pdk_characterization"
    assert artifact["characterized_width_um"] == task.device_characterization.width_um
    assert artifact["model_parameters_by_polarity"] == (
        task.device_characterization.model_parameters_by_polarity
    )
    assert artifact["raw_data_evidence_source"] == "eda_result"
    assert artifact["normalized_point_evidence_source"] == "software_inference"
    assert normalized["holdout_audit"]["passed"] is True
    assert normalized["bias_and_sign_consistency"] == "matched"


def test_normalized_real_artifact_is_consumed_by_generic_small_signal_core() -> None:
    task = _task()
    artifact = normalize_mos_characterization(task, _raw_result(task))["artifact"]
    point = next(
        item
        for item in artifact["points"]
        if item["polarity"] == "nmos"
        and item["length_um"] == 0.03
        and item["vgs_magnitude_v"] == 0.4
        and item["vds_magnitude_v"] == 0.3
        and item["vsb_magnitude_v"] == 0.0
    )
    request = SmallSignalNetworkRequest.model_validate(
        {
            "id": "real-artifact-contract-smoke",
            "characterization": artifact,
            "mosfets": [
                {
                    "name": "MN0",
                    "model": point["model"],
                    "point_id": point["id"],
                    "drain": "OUT",
                    "gate": "IN",
                    "source": "0",
                    "bulk": "0",
                    "width_um": 1.0,
                    "length_um": point["length_um"],
                    "vgs_magnitude_v": point["vgs_magnitude_v"],
                    "vds_magnitude_v": point["vds_magnitude_v"],
                    "vsb_magnitude_v": point["vsb_magnitude_v"],
                }
            ],
            "resistors": [
                {
                    "name": "RD",
                    "positive": "OUT",
                    "negative": "VDD",
                    "resistance_ohm": 20_000.0,
                }
            ],
            "boundary_voltages": [
                {"node": "IN", "voltage": {"real": 1.0}},
                {"node": "VDD", "voltage": {}},
            ],
            "input_expression": {"terms": {"IN": 1.0}},
            "output_expression": {"terms": {"OUT": 1.0}},
            "frequencies_hz": [1e3, 1e6, 1e9, 1e12, 1e15],
        }
    )

    result = analyze_small_signal_network(request)

    assert result.status is RunStatus.SUCCEEDED
    assert result.characterization_source.value == "pdk_characterization"
    assert result.characterization_raw_data_evidence_source.value == "eda_result"
    assert result.characterization_point_evidence_source.value == "software_inference"
    assert result.derived_mos_values[0].gm_s > 0.0


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("empty", "returned no operating points"),
        ("pmos_sign", "drain-current sign is invalid"),
        ("nonfinite", "is not finite"),
        ("manifest", "fingerprint does not match"),
        ("model_parameters", "header model_parameters_by_polarity"),
    ],
)
def test_normalization_rejects_empty_inconsistent_or_unbound_evidence(
    mutation: str, message: str
) -> None:
    task = _task()
    raw = _raw_result(task)
    if mutation == "empty":
        raw["points"] = []
    elif mutation == "pmos_sign":
        point = next(item for item in raw["points"] if item["polarity"] == "pmos")
        point["raw"]["ids_a"] = abs(point["raw"]["ids_a"])
    elif mutation == "nonfinite":
        raw["points"][0]["raw"]["gm_s"] = float("nan")
    elif mutation == "model_parameters":
        raw["model_parameters_by_polarity"]["nmos"]["ad"] = "4e-14"
    else:
        raw["evidence"]["manifest_sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match=message):
        normalize_mos_characterization(task, raw)


def test_executor_marks_failed_holdout_partial_without_discarding_raw_eda() -> None:
    task = _task()
    raw = _raw_result(task)
    holdout = next(item for item in raw["points"] if item["set"] == "holdout")
    holdout["raw"]["gm_s"] *= 10.0

    class CharacterizingAdapter(DeterministicDemoAdapter):
        def characterize_devices(self, _task: TaskSpec) -> AdapterResult:
            return AdapterResult(
                data=deepcopy(raw),
                evidence_source=EvidenceSource.EDA_RESULT,
            )

    plan = build_plan(task)
    record = TaskExecutor(CharacterizingAdapter()).execute(
        task,
        plan,
        token=plan.confirmation_token,
    )

    assert record.status is RunStatus.PARTIAL
    raw_action = next(item for item in record.actions if item.action == "device.characterize")
    audit_action = next(
        item for item in record.actions if item.action == "device.characterize.validate"
    )
    assert raw_action.evidence_source is EvidenceSource.EDA_RESULT
    assert audit_action.evidence_source is EvidenceSource.SOFTWARE_INFERENCE
    assert audit_action.details["holdout_audit"]["passed"] is False


def test_executor_preserves_empty_result_as_failed_system_event() -> None:
    task = _task()
    raw = _raw_result(task)
    raw["points"] = []

    class EmptyAdapter(DeterministicDemoAdapter):
        def characterize_devices(self, _task: TaskSpec) -> AdapterResult:
            return AdapterResult(raw, EvidenceSource.EDA_RESULT)

    plan = build_plan(task)
    record = TaskExecutor(EmptyAdapter()).execute(
        task,
        plan,
        token=plan.confirmation_token,
    )

    assert record.status is RunStatus.FAILED
    assert record.actions[-1].action == "device.characterize.validate"
    assert record.actions[-1].status == "failed"
    assert record.actions[-1].evidence_source is EvidenceSource.SYSTEM_EVENT


def test_executor_preserves_transport_interruption_as_system_event() -> None:
    task = _task()

    class InterruptedAdapter(DeterministicDemoAdapter):
        def characterize_devices(self, _task: TaskSpec) -> AdapterResult:
            raise AdapterInterrupted("WinError 10054 during remote OP readback")

    plan = build_plan(task)
    record = TaskExecutor(InterruptedAdapter()).execute(
        task,
        plan,
        token=plan.confirmation_token,
    )

    assert record.status is RunStatus.FAILED
    assert record.actions[-1].action == "device.characterize"
    assert record.actions[-1].evidence_source is EvidenceSource.SYSTEM_EVENT
    assert "WinError 10054" in record.actions[-1].details["error"]
