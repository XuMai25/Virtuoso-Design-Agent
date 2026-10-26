import json
from pathlib import Path

import pytest

from virtuoso_design_agent.schematic_drawing import (
    DrawingRequest, Placement, compile_drawing, compare_readback,
    require_executable_source, transform,
)
from virtuoso_design_agent.cli import main
from virtuoso_design_agent.models import TaskSpec
from virtuoso_design_agent.planner import build_plan
from virtuoso_design_agent.safety import authorize_execution, SafetyViolation
from virtuoso_design_agent.adapters.schematic_drawing_bridge import capture_symbol_catalog


EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "schematic-drawing"


def local_request():
    return DrawingRequest.model_validate({
        "description": json.loads((EXAMPLES / "differential-pair.local.json").read_text()),
        "symbols": json.loads((EXAMPLES / "symbols.local.json").read_text()),
    })


def task_data():
    data = local_request().model_dump(mode="json")
    # Unit fixture ONLY. No connection is made; live execution would reject
    # these nonexistent masters. This is not PDK evidence or a runnable example.
    data["description"]["source"].update(kind="image", sha256="a" * 64)
    data["symbols"]["evidence_source"] = "bridge_readback"
    return {"id": "unit_drawing", "operation": "schematic.create", "circuit": "schematic_drawing",
            "pdk_profile": "nics4304_tsmc18", "target": {"library": "Analog_VLSI", "cell": "vda_drawing_unit"},
            "schematic_drawing": data, "safety": {"allowed_library": "Analog_VLSI"}}


@pytest.mark.parametrize("orientation,expected", [
    ("R0", (12, 23)), ("R90", (7, 22)), ("R180", (8, 17)), ("R270", (13, 18)),
    ("MX", (12, 17)), ("MY", (8, 23)), ("MXR90", (13, 22)), ("MYR90", (7, 18)),
])
def test_oa_orientations_with_asymmetric_anchor(orientation, expected):
    assert transform((2, 3), Placement(at=(10, 20), orient=orientation)) == expected


def test_mirrored_pair_and_rotated_resistor_share_physical_tail():
    compiled = compile_drawing(local_request())
    assert compiled["terminal_positions"]["M1.D"] == (3, 2)
    assert compiled["terminal_positions"]["RTAIL.PLUS"] == (0, -1)
    assert compiled["physical_components"]["TAIL"] == 1
    assert compiled["physical_components"]["VSS"] == 3
    edges = [row["points"] for row in compiled["wire_segments"] if row["net"] == "TAIL"]
    assert edges == [[(-3, 0), (0, 0)], [(0, -1), (0, 0)], [(0, 0), (3, 0)]]
    assert not compiled["executable_source"]


@pytest.mark.parametrize("mutate,match", [
    (lambda d: d["drawing"].update(junctions=[]), "explicit junction"),
    (lambda d: d["drawing"]["placements"]["M0"].update(at=[-4.001, 1]), "off the declared grid"),
    (lambda d: d["drawing"]["routes"][0]["points"][1].update(terminal="D"), "disagrees with electrical"),
    (lambda d: d["electrical"]["instances"][0]["terminals"].pop("B"), "terminal names"),
    (lambda d: d["electrical"]["instances"][0]["parameters"].update(w=None), "incomplete parameters"),
    (lambda d: d["electrical"]["instances"][0].update(master=None), "missing explicit PDK"),
    (lambda d: d["drawing"]["labels"][0].update(at=[1, 3]), "floating junction/label"),
    (lambda d: d["electrical"]["nets"][-1].update(label_connection_reason=None), "disconnected physical"),
    (lambda d: d["uncertainties"].append({"id": "unknown_crossing", "category": "topology", "question": "Does the image show a dot?"}), "unresolved topology"),
])
def test_reject_unsafe_or_incomplete_drafts(mutate, match):
    data = local_request().model_dump(mode="json")
    mutate(data["description"])
    with pytest.raises(ValueError, match=match):
        compile_drawing(DrawingRequest.model_validate(data))


def test_different_net_crossing_is_not_accepted_as_a_visual_overpass():
    data = local_request().model_dump(mode="json")
    data["description"]["drawing"]["routes"][0]["points"] = [[-6, 1], [-6, 3], [0, 3], [0, 1], [-4, 1]]
    with pytest.raises(ValueError, match="different-net crossing"):
        compile_drawing(DrawingRequest.model_validate(data))


def test_same_net_four_way_crossing_requires_explicit_electrical_junction():
    data = local_request().model_dump(mode="json")
    desc = data["description"]
    desc["electrical"]["pins"].append({"name": "TAIL", "net": "TAIL", "direction": "inputOutput"})
    desc["drawing"]["pin_placements"]["TAIL"] = {"at": [0, 0.5]}
    branch = next(r for r in desc["drawing"]["routes"] if r["net"] == "TAIL" and len(r["points"]) == 2)
    branch["points"][0] = {"pin": "TAIL"}
    compiled = compile_drawing(DrawingRequest.model_validate(data))
    assert sum((0, 0) in row["points"] for row in compiled["wire_segments"] if row["net"] == "TAIL") == 4
    desc["drawing"]["junctions"] = []
    with pytest.raises(ValueError, match="explicit junction"):
        compile_drawing(DrawingRequest.model_validate(data))


def test_wire_cannot_run_through_other_terminal():
    data = local_request().model_dump(mode="json")
    data["description"]["drawing"]["routes"][0]["points"] = [[-6, 1], [-2, 1]]
    with pytest.raises(ValueError, match="crossing|touches terminal"):
        compile_drawing(DrawingRequest.model_validate(data))


def test_signal_cannot_be_silently_replaced_by_label_islands():
    data = local_request().model_dump(mode="json")
    drawing = data["description"]["drawing"]
    drawing["junctions"] = []
    drawing["routes"][4]["points"] = [[-3, 0], [-1, 0]]
    drawing["routes"].append({"id": "island", "net": "TAIL", "points": [[1, 0], [3, 0]]})
    drawing["labels"].extend([{"net": "TAIL", "at": [-1, 0]}, {"net": "TAIL", "at": [1, 0]}])
    with pytest.raises(ValueError, match="disconnected physical"):
        compile_drawing(DrawingRequest.model_validate(data))


def readback_fixture():
    return json.loads((EXAMPLES / "readback.local.json").read_text())


def test_independent_fixture_and_wire_segmentation_equivalence():
    data = readback_fixture()
    report = compare_readback(local_request(), data["schematic"], data["wire_polylines"], wire_records=data["wire_records"])
    assert report["passed"] and report["wire_geometry"] == "passed"
    assert report["input_evidence_source"] == "software_inference"
    assert not report["performance_verified"] and not report["netlist_verified"]
    assert not compare_readback(local_request(), data["schematic"])["passed"]
    partial = compare_readback(local_request(), data["schematic"], data["wire_polylines"])
    assert partial["topology_and_parameters_passed"] and not partial["passed"]
    assert partial["wire_net_binding"] == "not_supplied"


@pytest.mark.parametrize("mutate,match", [
    (lambda r: r["instances"].pop(), "instance set"),
    (lambda r: r["instances"][0].update(cell="wrong_master"), "master cell"),
    (lambda r: r["instances"][0]["terms"].update(G="INN"), "terminal-to-net"),
    (lambda r: r["instances"][0]["params"].pop("w"), "parameter missing"),
    (lambda r: r["instances"][0].update(nlAction="ignore"), "nlAction"),
    (lambda r: r["instances"][1].update(orient="R0"), "placement"),
    (lambda r: r["pins"]["OUTP"].update(direction="inputOutput"), "pin direction"),
    (lambda r: r["nets"].update(unexpected={}), "net set"),
])
def test_independent_readback_catches_electrical_and_parameter_drift(mutate, match):
    data = readback_fixture()
    mutate(data["schematic"])
    result = compare_readback(local_request(), data["schematic"], data["wire_polylines"])
    assert not result["passed"]
    assert any(match in item for item in result["mismatches"])


def test_matching_logical_nets_do_not_hide_a_missing_wire():
    data = readback_fixture()
    data["wire_polylines"].pop(4)
    result = compare_readback(local_request(), data["schematic"], data["wire_polylines"])
    assert not result["passed"] and result["wire_geometry"] == "failed"
    assert result["topology_and_parameters_passed"]


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(net="INN"),
    lambda r: r.update(purpose="flight"),
    lambda r: r.update(points=[[0, 0], [1, 1]]),
    lambda r: r.update(points=[[0, 0]]),
    lambda r: r.update(points=None),
])
def test_readback_requires_conductive_net_binding_and_valid_wire_shape(mutate):
    data = readback_fixture()
    mutate(data["wire_records"][0])
    result = compare_readback(local_request(), data["schematic"], data["wire_polylines"], wire_records=data["wire_records"])
    assert not result["passed"] and result["wire_net_binding"] == "failed"
    assert result["topology_and_parameters_passed"] and result["wire_geometry"] == "passed"


def test_same_authorization_and_token_contract_and_no_replacement():
    task = TaskSpec.model_validate(task_data())
    assert TaskSpec.model_validate_json(task.model_dump_json()) == task
    plan = build_plan(task)
    assert plan.requires_remote_write and not plan.requires_remote_compute
    with pytest.raises(SafetyViolation, match="allow_remote_write"):
        authorize_execution(task, plan, plan.confirmation_token)
    raw = task_data()
    raw["safety"]["allow_remote_write"] = True
    enabled = TaskSpec.model_validate(raw)
    with pytest.raises(SafetyViolation, match="token mismatch"):
        authorize_execution(enabled, build_plan(enabled), plan.confirmation_token)
    authorize_execution(enabled, build_plan(enabled), build_plan(enabled).confirmation_token)
    raw["safety"]["replace_existing"] = True
    with pytest.raises(ValueError, match="forbids replacement"):
        TaskSpec.model_validate(raw)
    raw = task_data()
    raw["schematic_drawing"]["description"]["electrical"]["instances"][0]["parameters"]["w"] = "3u"
    assert build_plan(TaskSpec.model_validate(raw)).confirmation_token != plan.confirmation_token


def test_example_geometry_cannot_become_an_executable_task():
    with pytest.raises(ValueError, match="local-only"):
        require_executable_source(local_request())
    raw = task_data()
    raw["schematic_drawing"] = local_request().model_dump(mode="json")
    with pytest.raises(ValueError, match="local-only"):
        TaskSpec.model_validate(raw)


def test_cli_offline_example_and_independent_compare(tmp_path, capsys):
    out = tmp_path / "review"
    assert main(["schematic-compile", str(EXAMPLES / "differential-pair.local.json"),
                 "--symbols", str(EXAMPLES / "symbols.local.json"), "--output", str(out),
                 "--library", "Analog_VLSI", "--cell", "vda_unit"]) == 0
    assert not (out / "task.json").exists()
    assert "NOT native PDK symbols" in (out / "preview.svg").read_text(encoding="utf-8")
    assert main(["schematic-compare", str(EXAMPLES / "differential-pair.local.json"),
                 "--symbols", str(EXAMPLES / "symbols.local.json"),
                 "--readback", str(EXAMPLES / "readback.local.json")]) == 0


def test_cli_blocked_draft_emits_no_writing_task(tmp_path):
    data = local_request().description.model_dump(mode="json")
    data["electrical"]["instances"][0]["parameters"]["w"] = None
    draft = tmp_path / "draft.json"
    draft.write_text(json.dumps(data))
    out = tmp_path / "blocked"
    assert main(["schematic-compile", str(draft), "--symbols", str(EXAMPLES / "symbols.local.json"),
                 "--output", str(out)]) == 2
    assert json.loads((out / "review.json").read_text())["status"] == "blocked"
    assert not (out / "task.json").exists()


def test_reviewed_task_uses_existing_executor_and_worker_boundary_without_simulation(tmp_path, monkeypatch):
    from virtuoso_design_agent.adapters.subprocess_bridge import SubprocessBridgeAdapter
    from virtuoso_design_agent.executor import TaskExecutor
    from virtuoso_design_agent.models import RunStatus

    data = task_data()["schematic_drawing"]
    description, symbols = tmp_path / "description.json", tmp_path / "symbols.json"
    description.write_text(json.dumps(data["description"]))
    symbols.write_text(json.dumps(data["symbols"]))
    output = tmp_path / "compiled"
    assert main(["schematic-compile", str(description), "--symbols", str(symbols),
                 "--output", str(output), "--library", "Analog_VLSI", "--cell", "vda_unit"]) == 0
    task = TaskSpec.model_validate_json((output / "task.json").read_text())
    assert not task.safety.allow_remote_write and not task.safety.allow_remote_compute
    task.safety.allow_remote_write = True  # fake transport below, not EDA authorization
    calls = []
    def request(self, action, payload, **kwargs):
        calls.append(action)
        if action != "probe":
            assert payload["schematic_drawing"] == task.schematic_drawing.model_dump(mode="json")
            assert "analysis" not in payload and not payload["replace_existing"]
        return {"fixture_only": True}
    monkeypatch.setattr(SubprocessBridgeAdapter, "_request", request)
    plan = build_plan(task)
    record = TaskExecutor(SubprocessBridgeAdapter()).execute(task, plan, token=plan.confirmation_token)
    assert record.status is RunStatus.SUCCEEDED
    assert calls == ["probe", "create_schematic_drawing", "inspect_schematic_drawing"]
    assert record.selected_metrics is None
    assert any("separate gates" in note for note in record.notes)


def test_symbol_capture_uses_real_named_pin_boxes_not_terminal_order():
    class Symbols:
        def read_ports(self, lib, cell, **kwargs):
            return {"terms": [{"name": "p", "numBits": 1, "bbox": [[1, 2], [3, 4]]},
                              {"name": "n", "numBits": 1, "bbox": [[-3, -4], [-1, -2]]}],
                    "selectionBoxes": []}
    class Client:
        symbol = Symbols()
    catalog = capture_symbol_catalog(Client(), local_request(), provenance="unit stub, not remote")
    assert catalog.symbols["example_nmos"].terminals == {"p": (2, 3), "n": (-2, -3)}
