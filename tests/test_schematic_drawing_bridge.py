"""Actual installed Bridge builders, but a fake client: NEVER connect to EDA.

Run additionally in Bridge's Python environment. The dependency-light VDA
environment may skip this file; geometry/contract tests need no Bridge install.
"""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("virtuoso_bridge")

from virtuoso_design_agent.schematic_drawing import DrawingRequest
from virtuoso_design_agent.adapters.schematic_drawing_bridge import create_drawing, read_wire_records
from virtuoso_design_agent.adapters.schematic_drawing_bridge import terminal_position_guards


EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "schematic-drawing"


def test_native_coordinate_guard_does_not_call_numeric_literal():
    # In SKILL, (0.9375) is a function call, not arithmetic grouping. This
    # exact coordinate appeared on the first real N28 image schematic.
    positive, negative = terminal_position_guards({"terminal_positions": {
        "M1.B": (-2, 0.9375), "M3.S": (0, -0.4375)}})
    assert "abs(xCoord(p) - -2)" in positive
    assert "abs(yCoord(p) - 0.9375)" in positive
    assert "abs(yCoord(p) - -0.4375)" in negative
    assert "(0.9375)" not in positive and "(-0.4375)" not in negative


def request_fixture():
    description = json.loads((EXAMPLES / "differential-pair.local.json").read_text())
    symbols = json.loads((EXAMPLES / "symbols.local.json").read_text())
    description["source"].update(kind="image", sha256="a" * 64)
    symbols["evidence_source"] = "bridge_readback"  # unit client only
    return DrawingRequest(description=description, symbols=symbols)


def setup_client(monkeypatch):
    import virtuoso_bridge.virtuoso.schematic.reader as reader
    fixture = json.loads((EXAMPLES / "readback.local.json").read_text())
    request = request_fixture()
    def read_ports(lib, cell, **kwargs):
        symbol = next(s for s in request.symbols.symbols.values() if s.master.cell == cell)
        return {"terms": [{"name": n, "bbox": [list(p), list(p)], "numBits": 1} for n, p in symbol.terminals.items()],
                "selectionBoxes": symbol.selection_boxes}
    client = SimpleNamespace(symbol=SimpleNamespace(read_ports=read_ports), batches=[], params=[], exists=False)
    def execute_operations(commands, **kwargs):
        client.batches.append(commands)
        if "Target exists" in commands[0]:
            if client.exists:
                return {"ok": False, "error": "Target exists; no overwrite or replay"}
            client.exists = True
        return {"ok": True, "result": {"status": "success"}}
    client.execute_operations = execute_operations
    def execute_skill(command, **kwargs):
        assert '"schematic" "schematic" "r"' in command and 'dbClose(cv)' in command
        assert '!member(s~>objType list("label" "ellipse"))' in command
        # Separate fixture net identities, not generated from the compiler.
        net_names = ["INP", "INN", "OUTP", "OUTN", "TAIL", "TAIL", "VSS", "VSS", "VSS"]
        rows = [f'("wire" "drawing" "{net}" (' + " ".join(f"({p[0]} {p[1]})" for p in line) + "))"
                for net, line in zip(net_names, fixture["wire_polylines"])]
        return SimpleNamespace(errors=[], output="(" + " ".join(rows) + ")")
    client.execute_skill = execute_skill
    client.schematic = SimpleNamespace(read=lambda *a, **k: copy.deepcopy(fixture["schematic"]))
    def read_placement(*args):
        return {"wires": ["(" + " ".join(f"({p[0]} {p[1]})" for p in line) + ")" for line in fixture["wire_polylines"]]}
    monkeypatch.setattr(reader, "read_placement", read_placement)
    def setter(client, lib, cell, instance, **params):
        assert params.pop("param_filters") is None
        assert params.pop("strict") is True
        client.params.append((instance, params))
        return params
    return request, client, setter, fixture


def test_public_builders_draw_continuous_wires_with_guard_and_readback(monkeypatch):
    request, client, setter, _ = setup_client(monkeypatch)
    result = create_drawing(client, request, "Analog_VLSI", "vda_unit", set_parameters=setter)
    assert result["comparison"]["passed"]
    assert len(client.batches) == 2 and len(client.params) == 3
    first, second = map(" ".join, client.batches)
    assert first.index("Target exists") < first.index("dbOpenCellView")
    assert '"schematic" "schematic" "a"' in first
    assert '"w"' not in first and "ddDeleteObj" not in first
    assert "dbTransformPoint" in second and "Placed terminal changed" in second
    assert second.index("Placed terminal changed") < second.index("schCreateWire(")
    assert 'schCreateWire(cv "draw"' in second
    assert second.count("schCreateWire(") == 10
    assert second.count("schCreateWireLabel(") == 3
    assert "dbCreateInstTerm" not in second  # no hidden logical-only terminal bindings
    assert '"input"' in second and '"output"' in second


def test_existing_target_refused_and_never_replayed(monkeypatch):
    request, client, setter, _ = setup_client(monkeypatch)
    client.exists = True
    with pytest.raises(RuntimeError, match="Target exists"):
        create_drawing(client, request, "Analog_VLSI", "vda_unit", set_parameters=setter)
    assert len(client.batches) == 1 and not client.params


def test_interrupted_parameter_write_preserves_target_and_rejects_create_replay(monkeypatch):
    request, client, setter, _ = setup_client(monkeypatch)
    def interrupted(*args, **kwargs):
        raise TimeoutError("write may have reached OA; readback required")
    with pytest.raises(TimeoutError):
        create_drawing(client, request, "Analog_VLSI", "vda_unit", set_parameters=interrupted)
    assert client.exists and len(client.batches) == 1
    with pytest.raises(RuntimeError, match="Target exists"):
        create_drawing(client, request, "Analog_VLSI", "vda_unit", set_parameters=setter)
    assert len(client.batches) == 2 and not client.params


def test_live_geometry_drift_stops_before_any_write(monkeypatch):
    request, client, setter, _ = setup_client(monkeypatch)
    original = client.symbol.read_ports
    def drift(*args, **kwargs):
        data = original(*args, **kwargs)
        data["terms"][0]["bbox"] = [[100, 100], [100, 100]]
        return data
    client.symbol.read_ports = drift
    with pytest.raises(ValueError, match="live master geometry differs"):
        create_drawing(client, request, "Analog_VLSI", "vda_unit", set_parameters=setter)
    assert not client.batches and not client.params


def test_command_success_cannot_mask_bad_readback(monkeypatch):
    request, client, setter, fixture = setup_client(monkeypatch)
    fixture["schematic"]["instances"][0]["terms"]["G"] = "INN"
    with pytest.raises(ValueError, match="terminal-to-net"):
        create_drawing(client, request, "Analog_VLSI", "vda_unit", set_parameters=setter)
    assert len(client.batches) == 2  # preserved, not rebuilt after failure


def test_parameter_name_remap_stops_before_routing(monkeypatch):
    request, client, _, _ = setup_client(monkeypatch)
    with pytest.raises(ValueError, match="CDF setter remapped"):
        create_drawing(client, request, "Analog_VLSI", "vda_unit", set_parameters=lambda *a, **k: {"other": "1"})
    assert len(client.batches) == 1


def test_decorative_lines_cannot_masquerade_as_conductive_wires(monkeypatch):
    request, client, setter, _ = setup_client(monkeypatch)
    original = client.execute_skill
    def annotation(*args, **kwargs):
        result = original(*args, **kwargs)
        result.output = result.output.replace('"wire"', '"annotate"')
        return result
    client.execute_skill = annotation
    with pytest.raises(ValueError, match="layer/purpose/net"):
        create_drawing(client, request, "Analog_VLSI", "vda_unit", set_parameters=setter)
