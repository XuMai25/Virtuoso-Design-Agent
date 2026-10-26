"""Small adapter to existing public Bridge builders/readers, not a transport layer.

The worker supplies the existing guarded client and its CDF setter. Nothing in
this module connects to a server at import time or during local compilation.
"""

from __future__ import annotations

from ..schematic_drawing import (
    DrawingRequest, SymbolCatalog, SymbolGeometry, compare_readback,
    fingerprint, require_executable_source,
)


def capture_symbol_catalog(client, request: DrawingRequest, *, provenance: str) -> SymbolCatalog:
    """Read named terminal bbox centers/selection boxes using Bridge's public API.

    The caller supplies explicit mappings in the request; this is read-only and
    intentionally never guesses a PDK mapping, terminal order, or CDF defaults.
    """
    symbols = {}
    for key, geometry in request.symbols.symbols.items():
        master = geometry.master
        raw = client.symbol.read_ports(master.library, master.cell, view=master.view)
        terminals = {}
        for term in raw.get("terms", []):
            bbox = term.get("bbox")
            if (not bbox or len(bbox) != 2 or any(len(p) != 2 for p in bbox)
                    or term.get("numBits") != 1 or term["name"] in terminals):
                raise ValueError(f"{key}: missing/ambiguous scalar terminal geometry")
            terminals[term["name"]] = tuple((float(bbox[0][i]) + float(bbox[1][i])) / 2 for i in (0, 1))
        if not terminals:
            raise ValueError(f"{key}: symbol has no readable terminal geometry")
        symbols[key] = SymbolGeometry(master=master, terminals=terminals,
                                      selection_boxes=raw.get("selectionBoxes", []))
    return SymbolCatalog(pdk_profile=request.description.pdk_profile,
                         evidence_source="bridge_readback", provenance=provenance,
                         symbols=symbols)


def _checked_batch(client, commands, timeout):
    from virtuoso_bridge.virtuoso.editor import ensure_operation_response
    response = client.execute_operations(commands, timeout=timeout)
    ensure_operation_response(response, context="explicit schematic drawing")


def _batch_prefix(library, cell, *, new):
    from virtuoso_bridge.virtuoso.editor import remember_editor_cellview
    from virtuoso_bridge.virtuoso.ops import open_cell_view, escape_skill_string
    lib, name = escape_skill_string(library), escape_skill_string(cell)
    # This guard runs INSIDE the same Bridge batch, before append-mode open.
    # Never use create()/mode w or delete a partially created target on failure.
    check = (f'when(ddGetObj("{lib}" "{name}" "schematic") error("Target exists; no overwrite or replay"))'
             if new else f'unless(ddGetObj("{lib}" "{name}" "schematic") error("Owned target is missing"))')
    return [check, open_cell_view(library, cell, view="schematic", mode="a"),
            'unless(cv error("Cannot open drawing target"))', remember_editor_cellview(opened=True)]


def _batch_suffix():
    from virtuoso_bridge.virtuoso.editor import close_editor_cellview
    from virtuoso_bridge.virtuoso.ops import save_current_cellview
    from virtuoso_bridge.virtuoso.schematic.ops import schematic_check
    return [schematic_check(), save_current_cellview(), close_editor_cellview()]


def terminal_position_guards(compiled):
    """Native post-CDF instance geometry CAS before wires, including transforms.

    This narrowly scoped SKILL is needed because read_ports reports master
    geometry, not the placed instance's potentially parameterized pin figure.
    Execution, error handling and cellview cleanup are still owned by Bridge.
    """
    from virtuoso_bridge.virtuoso.ops import escape_skill_string
    commands = []
    for key, (x, y) in compiled["terminal_positions"].items():
        instance, terminal = map(escape_skill_string, key.split(".", 1))
        commands.append(
            'let((i term pin fig box p) '
            f'i=car(setof(z cv~>instances z~>name == "{instance}")) '
            'unless(i error("Missing placed instance")) '
            f'term=car(setof(z i~>master~>terminals z~>name == "{terminal}")) '
            'unless(term error("Missing exact master terminal")) '
            'pin=car(term~>pins) fig=when(pin car(pin~>figs)) '
            'unless(fig error("Missing terminal figure")) box=fig~>bBox '
            'p=dbTransformPoint(centerBox(box) i~>transform) '
            f'unless(abs(xCoord(p) - {x:.12g})<1e-9 && abs(yCoord(p) - {y:.12g})<1e-9 '
            f'error("Placed terminal changed: {instance}.{terminal}; replan before routing")) t)'
        )
    return commands


def read_wire_records(client, library, cell):
    """Bridge's public placement reader omits line net/layer; bind these explicitly.

    Narrow read-only application query via the existing public SKILL transport.
    Native schCheck junction dots are netless ellipses, not conductive paths;
    exclude them just like labels. Unknown shape kinds still fail closed.
    """
    from virtuoso_bridge.virtuoso.ops import open_cell_view
    from virtuoso_bridge.virtuoso.skill_output import parse_sexpr, is_single_complete_skill_list
    command = ('let((cv rows) ' + open_cell_view(library, cell, view="schematic", mode="r")
               + ' unless(cv error("Cannot read conductive wires")) '
               'unwindProtect(progn(foreach(s cv~>shapes '
               'when(s~>layerName == "wire" && !member(s~>objType list("label" "ellipse")) '
               'unless(member(s~>objType list("line" "path")) error("Unsupported wire shape; preserve and inspect")) '
               'rows=cons(list(s~>layerName s~>purpose when(s~>net s~>net~>name) s~>points) rows))) '
               'reverse(rows)) dbClose(cv)))')
    result = client.execute_skill(command, timeout=60)
    if getattr(result, "errors", None):
        raise ValueError(f"conductive wire readback failed: {result.errors}")
    output = (getattr(result, "output", "") or "").strip()
    if output == "nil":
        return []
    if not is_single_complete_skill_list(output):
        raise ValueError("invalid conductive wire readback framing")
    rows = parse_sexpr(output)
    records = []
    for row in rows:
        if not isinstance(row, list) or len(row) != 4 or not isinstance(row[3], list):
            raise ValueError("invalid conductive wire record")
        records.append({"layer": row[0], "purpose": row[1], "net": row[2],
                        "points": row[3]})
    return records


def inspect_drawing(client, request, library, cell):
    from virtuoso_bridge.virtuoso.schematic.reader import read_placement
    from virtuoso_bridge.virtuoso.skill_output import parse_sexpr, is_single_complete_skill_list
    raw = client.schematic.read(library, cell, include_positions=True,
                                include_param_evidence=True, param_filters=None)
    placement = read_placement(client, library, cell)
    if any(not is_single_complete_skill_list(line) for line in placement["wires"]):
        raise ValueError("malformed independent wire readback")
    wires = [parse_sexpr(line) for line in placement["wires"]]
    records = read_wire_records(client, library, cell)
    comparison = compare_readback(request, raw, wires, evidence_source="bridge_readback", wire_records=records)
    if not comparison["passed"]:
        raise ValueError("independent schematic readback failed: " + "; ".join(comparison["mismatches"]))
    return {"bridge_schematic": raw, "placement_readback": placement,
            "conductive_wire_readback": records,
            "comparison": comparison, "drawing_sha256": fingerprint(request),
            "completion_scope": "structure/parameters/physical-wire coverage only; native readability, netlist and performance pending"}


def create_drawing(client, request: DrawingRequest, library: str, cell: str,
                   *, set_parameters, timeout=180):
    from virtuoso_bridge.virtuoso.schematic.ops import (
        schematic_create_inst_by_master_name, schematic_create_pin,
        schematic_create_wire, schematic_create_wire_label,
    )
    compiled = require_executable_source(request)
    actual = capture_symbol_catalog(client, request, provenance="fresh pre-write symbol readback")
    if actual.symbols != request.symbols.symbols:
        raise ValueError("live master geometry differs from reviewed catalog; no OA write performed")
    desc = request.description
    commands = _batch_prefix(library, cell, new=True)
    for dev in desc.electrical.instances:
        master = request.symbols.symbols[dev.master].master
        placement = desc.drawing.placements[dev.name]
        cmd = schematic_create_inst_by_master_name(master.library, master.cell, master.view,
                                                   dev.name, *placement.at, placement.orient)
        commands.append(f'unless({cmd} error("Instance creation failed"))')
    _checked_batch(client, commands + _batch_suffix(), timeout)
    for dev in desc.electrical.instances:
        if dev.parameters:
            applied = set_parameters(client, library, cell, dev.name,
                                     param_filters=None, strict=True, **dev.parameters)
            if set(applied) != set(dev.parameters):
                raise ValueError(f"{dev.name}: CDF setter remapped names; preserve partial target and replan")
    # Endpoints are checked against the actual post-callback symbol, not just
    # the preflight catalog; a parameterized symbol cannot silently break wires.
    commands = _batch_prefix(library, cell, new=False) + terminal_position_guards(compiled)
    for pin in desc.electrical.pins:
        placement = desc.drawing.pin_placements[pin.name]
        commands.append(f'unless({schematic_create_pin(pin.name, *placement.at, placement.orient, direction=pin.direction)} error("Pin creation failed"))')
    for segment in compiled["wire_segments"]:
        commands.append(f'unless({schematic_create_wire(segment["points"], route_style="draw")} error("Wire creation failed"))')
    for label in desc.drawing.labels:
        commands.append(f'unless({schematic_create_wire_label(*label.at, label.net, "lowerLeft", "R0")} error("Wire label creation failed"))')
    _checked_batch(client, commands + _batch_suffix(), timeout)
    result = inspect_drawing(client, request, library, cell)
    result.update(created=True, fresh_master_readback=actual.model_dump(mode="json"))
    return result
