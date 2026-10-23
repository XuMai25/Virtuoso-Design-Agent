"""Reviewed electrical intent and explicit geometry, without image recognition or EDA IO.

Coordinates are Virtuoso user units, x right/y up. An agent translates the image;
this module never infers a missing connection, parameter, or PDK device.
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
from html import escape
import json
import math
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr


Name = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_$!]*$", max_length=128)]
Number = Annotated[float, Field(strict=True, allow_inf_nan=False, ge=-100000, le=100000)]
Point = tuple[Number, Number]
Orientation = Literal["R0", "R90", "R180", "R270", "MX", "MY", "MXR90", "MYR90"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImageSource(Strict):
    kind: Literal["local_example", "image"]
    reference: str = Field(min_length=1)
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    interpretation_notes: str = Field(min_length=1)


class Uncertainty(Strict):
    id: Name
    category: Literal["topology", "parameter", "master", "layout"]
    question: str = Field(min_length=1)
    resolution: str | None = None


class Master(Strict):
    library: Name
    cell: Name
    view: Name = "symbol"


class SymbolGeometry(Strict):
    master: Master
    # Named terminals, never inferred positional/DRCG order.
    terminals: dict[Name, Point]
    selection_boxes: list[tuple[Point, Point]] = Field(default_factory=list)


class SymbolCatalog(Strict):
    schema_version: Literal[1] = 1
    pdk_profile: str = Field(min_length=1)
    evidence_source: Literal["bridge_readback", "software_inference"]
    provenance: str = Field(min_length=1)
    symbols: dict[Name, SymbolGeometry]


class Device(Strict):
    name: Name
    master: Name | None = None
    terminals: dict[Name, Name | None]
    parameters: dict[Name, StrictStr | None] = Field(default_factory=dict)
    parameters_complete: StrictBool = False
    parameter_review: str = ""


class Net(Strict):
    name: Name
    role: Literal["signal", "power", "ground", "cross_region"] = "signal"
    # Disconnected physical components require an explicit reason and labels.
    label_connection_reason: str | None = None


class Pin(Strict):
    name: Name
    net: Name
    direction: Literal["input", "output", "inputOutput"]


class Electrical(Strict):
    instances: list[Device] = Field(min_length=1, max_length=64)
    nets: list[Net] = Field(min_length=1, max_length=128)
    pins: list[Pin] = Field(default_factory=list, max_length=64)


class Placement(Strict):
    at: Point
    orient: Orientation = "R0"
    group: str = ""


class TerminalRef(Strict):
    instance: Name
    terminal: Name


class PinRef(Strict):
    pin: Name


class Route(Strict):
    id: Name
    net: Name
    points: list[Point | TerminalRef | PinRef] = Field(min_length=2, max_length=64)


class NetPoint(Strict):
    net: Name
    at: Point


class Drawing(Strict):
    coordinate_system: Literal["virtuoso_user_units_x_right_y_up"] = "virtuoso_user_units_x_right_y_up"
    grid: float = Field(ge=0.000001, le=100, strict=True, allow_inf_nan=False)
    placements: dict[Name, Placement]
    pin_placements: dict[Name, Placement] = Field(default_factory=dict)
    routes: list[Route] = Field(default_factory=list, max_length=256)
    junctions: list[NetPoint] = Field(default_factory=list, max_length=128)
    labels: list[NetPoint] = Field(default_factory=list, max_length=128)


class SchematicDescription(Strict):
    schema_version: Literal[1] = 1
    id: Name
    pdk_profile: str = Field(min_length=1)
    source: ImageSource
    electrical: Electrical
    drawing: Drawing
    uncertainties: list[Uncertainty] = Field(default_factory=list)


class DrawingRequest(Strict):
    description: SchematicDescription
    symbols: SymbolCatalog


def fingerprint(value) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def transform(point: Point, placement: Placement) -> Point:
    """The eight OA orthogonal orientations; mirrors precede the 90deg rotation."""
    x, y = point
    a, b, c, d = {
        "R0": (1, 0, 0, 1), "R90": (0, -1, 1, 0),
        "R180": (-1, 0, 0, -1), "R270": (0, 1, -1, 0),
        "MX": (1, 0, 0, -1), "MY": (-1, 0, 0, 1),
        "MXR90": (0, 1, 1, 0), "MYR90": (0, -1, -1, 0),
    }[placement.orient]
    return (round(a*x + b*y + placement.at[0], 9),
            round(c*x + d*y + placement.at[1], 9))


def _index(rows, field="name"):
    result = {getattr(row, field): row for row in rows}
    if len(result) != len(rows):
        raise ValueError(f"duplicate {field}")
    return result


def _on(point, a, b):
    return ((a[0] == b[0] == point[0] and min(a[1], b[1]) <= point[1] <= max(a[1], b[1]))
            or (a[1] == b[1] == point[1] and min(a[0], b[0]) <= point[0] <= max(a[0], b[0])))


def _intersections(a, b, c, d):
    if a[0] == b[0] and c[1] == d[1]:
        p = (a[0], c[1])
        return [p] if _on(p, a, b) and _on(p, c, d) else []
    if a[1] == b[1] and c[0] == d[0]:
        return _intersections(c, d, a, b)
    return sorted({p for p in (a, b, c, d) if _on(p, a, b) and _on(p, c, d)})


def _split(segments, cuts):
    edges = set()
    for a, b in segments:
        points = sorted({a, b, *(p for p in cuts if _on(p, a, b))})
        edges.update(tuple(sorted((p, q))) for p, q in zip(points, points[1:]))
    return edges


def compile_drawing(request: DrawingRequest) -> dict:
    """Compile an explicit drawing or refuse; inference fixtures remain non-executable."""
    desc, catalog = request.description, request.symbols
    errors = []
    if desc.pdk_profile != catalog.pdk_profile:
        errors.append("PDK profile and geometry catalog differ")
    if desc.source.kind == "image" and desc.source.sha256 is None:
        errors.append("image source requires a SHA-256 binding")
    for item in desc.uncertainties:
        if not item.resolution or not item.resolution.strip():
            errors.append(f"unresolved {item.category}: {item.id}: {item.question}")
    devices = _index(desc.electrical.instances)
    nets = _index(desc.electrical.nets)
    pins = _index(desc.electrical.pins)
    _index(desc.drawing.routes, "id")
    if set(devices) != set(desc.drawing.placements):
        errors.append("instance placements must exactly match electrical instances")
    if set(pins) != set(desc.drawing.pin_placements):
        errors.append("pin placements must exactly match electrical pins")
    for item in devices.values():
        if item.master not in catalog.symbols:
            errors.append(f"{item.name}: missing explicit PDK master mapping/geometry")
            continue
        symbol = catalog.symbols[item.master]
        if not symbol.terminals or set(item.terminals) != set(symbol.terminals):
            errors.append(f"{item.name}: terminal names do not exactly match the master")
        if any(net not in nets for net in item.terminals.values()):
            errors.append(f"{item.name}: missing/unknown terminal net")
        if (not item.parameters_complete or not item.parameter_review.strip()
                or any(v is None or not v.strip() for v in item.parameters.values())):
            errors.append(f"{item.name}: incomplete parameters/model review; no defaults inferred")
        # Bridge has control keyword/shorthand names; don't silently remap them.
        if set(item.parameters) & {"param_filters", "strict", "wf"}:
            errors.append(f"{item.name}: use exact CDF names, not Bridge control keys/shorthands")
    for pin in pins.values():
        if pin.net not in nets or pin.name != pin.net:
            errors.append(f"{pin.name}: v1 requires a top-level pin named after its declared net")
    if errors:
        raise ValueError("; ".join(errors))

    def point(value):
        p = tuple(round(x, 9) for x in value)
        if any(not math.isclose(x / desc.drawing.grid, round(x / desc.drawing.grid),
                                rel_tol=0, abs_tol=1e-7) for x in p):
            raise ValueError(f"point {p} is off the declared grid; adjust spacing explicitly")
        return p

    terminals = {}
    anchors = defaultdict(list)
    for name, dev in devices.items():
        point(desc.drawing.placements[name].at)
        for term, offset in catalog.symbols[dev.master].terminals.items():
            p = point(transform(offset, desc.drawing.placements[name]))
            terminals[f"{name}.{term}"] = p
            anchors[dev.terminals[term]].append(p)
    for pin in pins.values():
        anchors[pin.net].append(point(desc.drawing.pin_placements[pin.name].at))

    def resolve(value, net):
        if isinstance(value, TerminalRef):
            if value.instance not in devices or devices[value.instance].terminals.get(value.terminal) != net:
                raise ValueError(f"route {net}: terminal anchor disagrees with electrical intent")
            return terminals[f"{value.instance}.{value.terminal}"]
        if isinstance(value, PinRef):
            if value.pin not in pins or pins[value.pin].net != net:
                raise ValueError(f"route {net}: pin anchor disagrees with electrical intent")
            return point(desc.drawing.pin_placements[value.pin].at)
        return point(value)

    segments = []
    routes = []
    for route in desc.drawing.routes:
        if route.net not in nets:
            raise ValueError(f"unknown route net {route.net}")
        points = [resolve(p, route.net) for p in route.points]
        for a, b in zip(points, points[1:]):
            if a == b or (a[0] != b[0] and a[1] != b[1]):
                raise ValueError(f"{route.id}: zero-length/diagonal segment; supply explicit orthogonal bends")
            segments.append((route.net, a, b))
        routes.append({"id": route.id, "net": route.net, "points": points})
    if len(segments) > 1024:
        raise ValueError("v1 supports at most 1024 explicit segments; split the design review")
    junctions = {(item.net, point(item.at)) for item in desc.drawing.junctions}
    if len(junctions) != len(desc.drawing.junctions):
        raise ValueError("duplicate junction")
    labels = [(item.net, point(item.at)) for item in desc.drawing.labels]
    for net, p in [*junctions, *labels]:
        if net not in nets or not any(n == net and _on(p, a, b) for n, a, b in segments):
            raise ValueError(f"{net} at {p}: floating junction/label")
    cuts = {p for _, a, b in segments for p in (a, b)}
    cuts.update(p for values in anchors.values() for p in values)
    cuts.update(p for _, p in [*junctions, *labels])
    for i, (net, a, b) in enumerate(segments):
        for other, c, d in segments[i+1:]:
            intersections = _intersections(a, b, c, d)
            if intersections and other != net:
                raise ValueError(f"different-net crossing/overlap: {net}/{other} at {intersections}; reroute or explicitly use cross-region labels")
            cuts.update(intersections)
        for other, points in anchors.items():
            if other != net and any(_on(p, a, b) for p in points):
                raise ValueError(f"{net}: wire touches terminal/pin of {other}")
    edges_by_net = {}
    components = {}
    for net, spec in nets.items():
        edges = _split([(a, b) for n, a, b in segments if n == net], cuts)
        graph = defaultdict(set)
        for a, b in edges:
            graph[a].add(b)
            graph[b].add(a)
        if not graph or any(p not in graph for p in anchors[net]):
            raise ValueError(f"{net}: unconnected terminal/pin or no physical wire")
        for p, neighbors in graph.items():
            if len(neighbors) > 2 and (net, p) not in junctions:
                raise ValueError(f"{net}: branch at {p} needs an explicit junction")
            if len(neighbors) == 1 and p not in anchors[net] and (net, p) not in labels:
                raise ValueError(f"{net}: dangling wire at {p}")
        for n, p in junctions:
            if n == net and len(graph[p]) < 3:
                raise ValueError(f"{net}: declared junction {p} is not a branch")
        unseen = set(graph)
        groups = []
        while unseen:
            pending, visited = [min(unseen)], set()
            while pending:
                p = pending.pop()
                if p not in visited:
                    visited.add(p)
                    pending.extend(graph[p] - visited)
            unseen -= visited
            groups.append(visited)
        if len(groups) > 1 and (spec.role == "signal" or not spec.label_connection_reason
                                or not spec.label_connection_reason.strip()):
            raise ValueError(f"{net}: disconnected physical branches; signal nets require continuous wires")
        for group in groups:
            if not any(p in group for p in anchors[net]):
                raise ValueError(f"{net}: orphan wire component")
            named = any(n == net and p in group for n, p in labels) or any(
                pin.net == net and point(desc.drawing.pin_placements[pin.name].at) in group for pin in pins.values())
            if not named:
                raise ValueError(f"{net}: each physical component needs a declared attached label or top pin")
        edges_by_net[net] = sorted(edges)
        components[net] = len(groups)
    return {
        "schema_version": 1, "evidence_source": "software_inference",
        "description_sha256": fingerprint(desc), "symbols_sha256": fingerprint(catalog),
        "executable_source": catalog.evidence_source == "bridge_readback" and desc.source.kind == "image",
        "terminal_positions": terminals, "routes": routes,
        "wire_segments": [{"net": n, "points": [a, b]} for n in sorted(edges_by_net) for a, b in edges_by_net[n]],
        "physical_components": components,
        "acceptance": {"local_connectivity": "passed", "parameters": "declared_complete_not_eda_verified",
                       "native_readability": "pending_human_review", "oa_readback": "not_run", "netlist": "not_run", "performance": "not_run"},
    }


def require_executable_source(request: DrawingRequest) -> dict:
    compiled = compile_drawing(request)
    if not compiled["executable_source"]:
        raise ValueError("example/inferred geometry is local-only; executable task requires image and Bridge symbol readback")
    return compiled


def _parameter_equal(expected, actual):
    if str(expected) == str(actual):
        return True
    pattern = r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)([fpnumkMG]?)"
    left, right = re.fullmatch(pattern, str(expected)), re.fullmatch(pattern, str(actual))
    if not left or not right:
        return False
    factors = {"": 1, "f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "M": 1e6, "G": 1e9}
    return math.isclose(float(left[1])*factors[left[2]], float(right[1])*factors[right[2]], rel_tol=1e-9, abs_tol=0)


def compare_readback(request: DrawingRequest, schematic: dict, wire_polylines: list | None = None,
                     *, evidence_source: Literal["bridge_readback", "software_inference"] = "software_inference",
                     wire_records: list[dict] | None = None) -> dict:
    """Compare independent Bridge data, not a dictionary fabricated from the plan."""
    compiled = compile_drawing(request)
    expected = request.description.electrical
    mismatches = []
    actual = {item["name"]: item for item in schematic.get("instances", [])}
    if len(actual) != len(schematic.get("instances", [])) or set(actual) != {i.name for i in expected.instances}:
        mismatches.append("instance set differs")
    for dev in expected.instances:
        item = actual.get(dev.name, {})
        master = request.symbols.symbols[dev.master].master
        for field, value in (("lib", master.library), ("cell", master.cell), ("view", master.view)):
            if item.get(field) != value:
                mismatches.append(f"{dev.name}: master {field} differs")
        if item.get("terms") != dev.terminals or item.get("nlAction"):
            mismatches.append(f"{dev.name}: terminal-to-net mapping or nlAction differs")
        placement = request.description.drawing.placements[dev.name]
        if tuple(item.get("xy", [])) != placement.at or item.get("orient") != placement.orient:
            mismatches.append(f"{dev.name}: placement differs")
        values = item.get("param_evidence", {}).get("cdf")
        if not isinstance(values, dict):
            values = item.get("params", {})
        for name, value in dev.parameters.items():
            if name not in values or not _parameter_equal(value, values[name]):
                mismatches.append(f"{dev.name}.{name}: parameter missing/different")
    if set(schematic.get("nets", {})) != {net.name for net in expected.nets}:
        mismatches.append("net set differs")
    actual_pins = schematic.get("pins", {})
    if set(actual_pins) != {pin.name for pin in expected.pins}:
        mismatches.append("top-level pin set differs")
    for pin in expected.pins:
        item = actual_pins.get(pin.name, {})
        if item.get("direction") != pin.direction or item.get("net") != pin.net or item.get("numBits") != 1:
            mismatches.append(f"{pin.name}: pin direction/net/width differs")
    topology_and_parameters_passed = not mismatches
    geometry = "not_supplied"
    if wire_polylines is not None:
        wanted = [(tuple(row["points"][0]), tuple(row["points"][1])) for row in compiled["wire_segments"]]
        observed = []
        for line in wire_polylines:
            if not isinstance(line, (list, tuple)) or len(line) < 2:
                mismatches.append("invalid/empty readback wire")
                continue
            for a, b in zip(line, line[1:]):
                if (not isinstance(a, (list, tuple)) or not isinstance(b, (list, tuple))
                        or len(a) != 2 or len(b) != 2):
                    mismatches.append("invalid readback wire coordinate")
                    continue
                try:
                    a, b = tuple(float(v) for v in a), tuple(float(v) for v in b)
                except (TypeError, ValueError):
                    mismatches.append("invalid readback wire coordinate")
                    continue
                if any(not math.isfinite(v) for v in (*a, *b)) or a == b or (a[0] != b[0] and a[1] != b[1]):
                    mismatches.append("invalid/diagonal readback wire")
                    continue
                observed.append((a, b))
        cuts = {p for edge in wanted + observed for p in edge}
        geometry = "passed" if _split(wanted, cuts) == _split(observed, cuts) else "failed"
        if geometry != "passed":
            mismatches.append("physical wire coverage differs (labels cannot substitute)")
    wire_net_binding = "not_supplied"
    if wire_records is not None:
        bound_edges = defaultdict(list)
        wire_net_binding = "passed"
        for row in wire_records:
            if (not isinstance(row, dict) or row.get("layer") != "wire"
                    or row.get("purpose") != "drawing" or row.get("net") not in {n.name for n in expected.nets}):
                mismatches.append("wire has invalid layer/purpose/net binding")
                wire_net_binding = "failed"
                continue
            try:
                points = [tuple(float(v) for v in p) for p in row["points"]]
            except (KeyError, TypeError, ValueError):
                points = []
            if (len(points) < 2 or any(len(p) != 2 or not all(math.isfinite(v) for v in p) for p in points)
                    or any(a == b or (a[0] != b[0] and a[1] != b[1]) for a, b in zip(points, points[1:]))):
                mismatches.append("invalid bound wire geometry")
                wire_net_binding = "failed"
                continue
            bound_edges[row["net"]].extend(zip(points, points[1:]))
        for net in expected.nets:
            wanted = [(tuple(row["points"][0]), tuple(row["points"][1])) for row in compiled["wire_segments"] if row["net"] == net.name]
            observed = bound_edges[net.name]
            cuts = {p for edge in wanted + observed for p in edge}
            if _split(wanted, cuts) != _split(observed, cuts):
                mismatches.append(f"{net.name}: conductive wire-to-net coverage differs")
                wire_net_binding = "failed"
    return {"passed": not mismatches and geometry == "passed" and wire_net_binding == "passed",
            "topology_and_parameters_passed": topology_and_parameters_passed,
            "mismatches": mismatches, "wire_geometry": geometry,
            "wire_net_binding": wire_net_binding,
            "evidence_source": "software_inference", "input_evidence_source": evidence_source,
            "readback_sha256": fingerprint(schematic),
            "wire_readback_sha256": fingerprint({"polylines": wire_polylines, "records": wire_records}),
            "netlist_verified": False, "performance_verified": False,
            "readability": "pending_native_human_review"}


def render_preview(request: DrawingRequest, compiled: dict) -> str:
    """An explicitly non-native SVG review aid, not a PDK-symbol reconstruction."""
    positions = [p for row in compiled["wire_segments"] for p in row["points"]]
    positions += [p.at for p in request.description.drawing.placements.values()]
    x0, x1 = min(p[0] for p in positions)-1, max(p[0] for p in positions)+1
    y0, y1 = min(p[1] for p in positions)-1, max(p[1] for p in positions)+1
    scale = 50
    def xy(p):
        return (round((p[0]-x0)*scale, 4), round((y1-p[1])*scale+35, 4))
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{max(750,(x1-x0)*scale+150)}" height="{(y1-y0)*scale+75}" role="img">',
             '<rect width="100%" height="100%" fill="white"/>',
             '<text x="12" y="20" font-size="13">LOCAL REVIEW — software_inference; boxes are NOT native PDK symbols</text>']
    for row in compiled["wire_segments"]:
        a, b = map(xy, row["points"])
        parts.append(f'<path d="M {a[0]} {a[1]} L {b[0]} {b[1]}" fill="none" stroke="#164e63" stroke-width="2"><title>{escape(row["net"])}</title></path>')
    for dev in request.description.electrical.instances:
        pos = request.description.drawing.placements[dev.name]
        symbol = request.symbols.symbols[dev.master]
        for box in symbol.selection_boxes:
            corners = [xy(transform(p, pos)) for p in box]
            x, y = min(p[0] for p in corners), min(p[1] for p in corners)
            w, h = abs(corners[0][0]-corners[1][0]), abs(corners[0][1]-corners[1][1])
            parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="white" fill-opacity="0.8" stroke="#64748b" stroke-dasharray="4 2"/>')
        x, y = xy(pos.at)
        parts.append(f'<text x="{x+6}" y="{y+16}" font-size="12">{escape(dev.name)} ({pos.orient})</text>')
        for name in dev.terminals:
            x, y = xy(compiled["terminal_positions"][f"{dev.name}.{name}"])
            parts.append(f'<circle cx="{x}" cy="{y}" r="3" fill="#0f766e"/><text x="{x+4}" y="{y-4}" font-size="10">{escape(name)}</text>')
    for p in request.description.drawing.junctions:
        x, y = xy(p.at)
        parts.append(f'<circle cx="{x}" cy="{y}" r="4" fill="#164e63"/>')
    for p in request.description.drawing.labels:
        x, y = xy(p.at)
        parts.append(f'<text x="{x+4}" y="{y-6}" font-size="12">{escape(p.net)}</text>')
    for pin in request.description.electrical.pins:
        x, y = xy(request.description.drawing.pin_placements[pin.name].at)
        parts.append(f'<text x="{x+4}" y="{y-6}" font-size="12">{escape(pin.name)} [{pin.direction}]</text>')
    return "\n".join(parts + ["</svg>"])
