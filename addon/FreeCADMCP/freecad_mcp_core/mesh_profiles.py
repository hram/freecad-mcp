"""Recover circular arcs in layered extruded profiles, using only BREP geometry."""
import math
from typing import Any, Callable

import FreeCAD as App
import Part
from .mesh_surfaces import fit_circle, simplify_contour, solve3


def fit_arc(points: list[tuple[float, float]], tolerance: float,
            min_angle_deg: float = 45) -> dict | None:
    """Endpoint-preserving circle fit for a consistently turning partial arc."""
    if len(points) < 5:
        return None
    ox, oy = points[0]
    samples = [points[0], points[len(points)//2], points[-1]]
    rows = [[x-ox, y-oy, 1] for x, y in samples]
    try:
        a, b, c = solve3(rows, [(x-ox)**2+(y-oy)**2 for x, y in samples])
    except ValueError:
        return None
    radius2 = c+(a*a+b*b)/4
    if radius2 <= 0:
        return None
    radius = math.sqrt(radius2)
    cx, cy = ox+a/2, oy+b/2
    error = max(abs(math.hypot(x-cx, y-cy)-radius) for x, y in points)
    if error > tolerance:
        return None
    angles = [math.atan2(y-cy, x-cx) for x, y in points]
    steps = [((b-a+math.pi) % (2*math.pi))-math.pi for a, b in zip(angles, angles[1:])]
    if not steps or min(steps)*max(steps) <= 0 or max(abs(s) for s in steps) > math.pi/6+1e-6:
        return None
    span = abs(sum(steps))
    if span < math.radians(min_angle_deg)-1e-6 or span >= 2*math.pi-1e-5:
        return None
    return {'center': (cx, cy), 'radius_mm': radius, 'fit_error_mm': error,
            'angle_deg': math.degrees(span),
            'chord_deviation_mm': radius*(1-math.cos(max(abs(s) for s in steps)/2))+error}


def find_arcs(points: list[tuple[float, float]], tolerance: float) -> list[dict]:
    """Find maximal non-overlapping arc chains, including across a wire's seam."""
    n = len(points)
    if n < 5 or fit_circle(points, tolerance):
        return []
    doubled = points+points
    candidates = []
    for start in range(n):
        best = None
        for count in range(5, n+1):
            fitted = fit_arc(doubled[start:start+count], tolerance, min_angle_deg=0)
            if fitted:
                if fitted['angle_deg'] >= 45-1e-6:
                    best = {**fitted, 'start': start, 'edges': count-1}
            else:
                break
        if best:
            candidates.append(best)
    accepted, used = [], set()
    for c in sorted(candidates, key=lambda c: (-c['edges'], c['start'])):
        indices = {(c['start']+i) % n for i in range(c['edges'])}
        if not indices.intersection(used):
            accepted.append(c)
            used.update(indices)
    return accepted


def rebuild_wire(wire: Any, axis: Any, u: Any, v: Any, height: float,
                 fit_tolerance: float, surface_tolerance: float) -> tuple[Any, list[dict]]:
    # Keep already analytic wires exactly as they are; do not polygonize them.
    if any(not isinstance(e.Curve, Part.Line) for e in wire.Edges):
        return wire, []
    points = simplify_contour([(p.Point.dot(u), p.Point.dot(v)) for p in wire.OrderedVertexes],
                              min(fit_tolerance*.01, 1e-5))
    arcs = [a for a in find_arcs(points, fit_tolerance)
            if a['chord_deviation_mm'] <= surface_tolerance]
    if not arcs:
        return wire, []
    n = len(points)
    starts = {a['start']: a for a in arcs}
    first = min(starts)

    def point(index: int) -> Any:
        x, y = points[index % n]
        return u*x+v*y+axis*height

    edges, consumed = [], 0
    while consumed < n:
        index = (first+consumed) % n
        if index in starts:
            a = starts[index]
            edges.append(Part.Arc(point(index), point(index+a['edges']//2),
                                  point(index+a['edges'])).toShape())
            consumed += a['edges']
        else:
            edges.append(Part.makeLine(point(index), point(index+1)))
            consumed += 1
    result = Part.Wire(edges)
    if not result.isClosed():
        raise ValueError('Arc profile did not close')
    return result, [{k: value for k, value in a.items() if k not in ('start', 'edges')}
                    for a in arcs]


def restore_extruded_profiles(shape: Any, fit_tolerance: float, surface_tolerance: float,
                              checkpoint: Callable) -> tuple[Any, dict]:
    """Rebuild only solids proven to consist of parallel extruded planar layers."""
    report = {'applied': False, 'arcs': [],
              'scope': 'Layered extrusions only; partial arcs >=45 degrees with >=4 small chord segments'}
    planes = [f for f in shape.Faces if isinstance(f.Surface, Part.Plane)]
    if not planes:
        return shape, report
    axis = max(planes, key=lambda f: f.Area).normalAt(0, 0)
    for component in (axis.x, axis.y, axis.z):
        if abs(component) > 1e-8:
            if component < 0:
                axis = -axis
            break
    max_drift = 0.0
    for f in shape.Faces:
        if isinstance(f.Surface, Part.Plane):
            dot = abs(f.normalAt(0, 0).dot(axis))
            # Tiny angular noise on a long narrow triangle is not a real
            # tapered wall. Bound the implied lateral drift in millimetres.
            heights = [p.Point.dot(axis) for p in f.Vertexes]
            span = max(heights)-min(heights)
            drift = dot*span/math.sqrt(max(1-dot*dot, 1e-30)) if dot < .5 else 0
            max_drift = max(max_drift, drift)
            if (dot < .5 and drift > fit_tolerance) or (dot >= .5 and abs(1-dot) > 1e-6):
                return shape, {**report, 'reason': 'Non-extruded or oblique side surface'}
        elif isinstance(f.Surface, Part.Cylinder):
            if abs(f.Surface.Axis.dot(axis)) < 1-1e-7:
                return shape, {**report, 'reason': 'Cylinder crosses the extrusion axis'}
        else:
            return shape, {**report, 'reason': 'Unsupported non-prismatic surface'}
    seed = App.Vector(1, 0, 0) if abs(axis.x) < .8 else App.Vector(0, 1, 0)
    u = axis.cross(seed)
    u.normalize()
    v = axis.cross(u)
    levels = []
    for value in sorted(p.Point.dot(axis) for p in shape.Vertexes):
        if not levels or value-levels[-1] > 1e-6:
            levels.append(value)
    if len(levels) > 32:
        return shape, {**report, 'reason': 'Too many extrusion levels'}
    slabs, recovered = [], []
    try:
        for low, high in zip(levels, levels[1:]):
            if high-low < 1e-6:
                continue
            checkpoint('reconstructing_arc_profile', .7)
            height = (low+high)/2
            wires = shape.slice(axis, height)
            if not wires:
                continue
            rebuilt = []
            for wire in wires:
                new_wire, arcs = rebuild_wire(wire, axis, u, v, height,
                                               fit_tolerance, surface_tolerance)
                rebuilt.append(new_wire)
                recovered.extend({**{k: value for k, value in a.items() if k != 'center'},
                    'profile_center_mm': a['center'],
                    'axis_point': list(u*a['center'][0]+v*a['center'][1]),
                    'axial_range_mm': [low, high], 'axis': list(axis)} for a in arcs)
            face = Part.makeFace(rebuilt, 'Part::FaceMakerBullseye')
            face.translate(axis*(low-height))
            slabs.append(face.extrude(axis*(high-low)))
        if not recovered:
            return shape, report
        proposed = slabs[0]
        for slab in slabs[1:]:
            proposed = proposed.fuse(slab)
        proposed = proposed.removeSplitter()
        if not proposed.isValid() or len(proposed.Solids) != len(shape.Solids):
            raise ValueError('Arc reconstruction changed solid count or produced invalid geometry')
        return proposed, {**report, 'applied': True, 'arcs': recovered, 'layers': len(slabs),
                          'max_source_side_drift_mm': max_drift}
    except Exception as exc:
        if getattr(exc, 'code', '') == 'JOB_CANCELLED':
            raise
        return shape, {**report, 'reason': str(exc)}
