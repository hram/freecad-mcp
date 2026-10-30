"""Recover full cylinder bands and layered extruded arc profiles from geometry.

Cones, freeform surfaces and unsupported intersecting profiles stay faceted.
"""
import math
from typing import Any, Callable

import FreeCAD as App
import Part


def solve3(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    rows = [list(a) + [b] for a, b in zip(matrix, rhs)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda i: abs(rows[i][col]))
        if abs(rows[pivot][col]) < 1e-14:
            raise ValueError("Degenerate circle samples")
        rows[col], rows[pivot] = rows[pivot], rows[col]
        scale = rows[col][col]
        rows[col] = [v / scale for v in rows[col]]
        for i in range(3):
            if i != col:
                scale = rows[i][col]
                rows[i] = [a-scale*b for a, b in zip(rows[i], rows[col])]
    return [r[3] for r in rows]


def simplify_contour(points: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    """Discard duplicate/collinear subdivision vertices, not polygon corners."""
    result = list(points)
    changed = True
    while changed and len(result) > 3:
        changed = False
        for i, b in enumerate(result):
            a, c = result[i-1], result[(i+1) % len(result)]
            dx, dy = c[0]-a[0], c[1]-a[1]
            length2 = dx*dx+dy*dy
            if length2 <= tolerance*tolerance:
                redundant = math.hypot(b[0]-a[0], b[1]-a[1]) <= tolerance
            else:
                position = ((b[0]-a[0])*dx+(b[1]-a[1])*dy)/length2
                distance = abs((b[0]-a[0])*dy-(b[1]-a[1])*dx)/math.sqrt(length2)
                redundant = 0 <= position <= 1 and distance <= tolerance
            if redundant:
                result.pop(i)
                changed = True
                break
    return result


def fit_circle(points: list[tuple[float, float]], tolerance: float,
               _linear_tolerance: float | None = None) -> dict | None:
    """Fit a full polygonal circle; reject coarse polygons and incomplete arcs."""
    source_count = len(points)
    linear_tolerance = min(tolerance*.01, 1e-5) if _linear_tolerance is None else _linear_tolerance
    original_points = points
    # A boolean intersection can insert points in the middle of a polygon
    # edge. Those lie on its chord, not on the original circle. Fitting them
    # as circle samples incorrectly rejects otherwise valid round contours.
    points = simplify_contour(points, linear_tolerance)
    if len(points) < 12:
        return None
    ox = sum(p[0] for p in points)/len(points)
    oy = sum(p[1] for p in points)/len(points)
    rows = [[x-ox, y-oy, 1] for x, y in points]
    rhs = [(x-ox)**2+(y-oy)**2 for x, y in points]
    try:
        a, b, c = solve3([[sum(r[i]*r[j] for r in rows) for j in range(3)] for i in range(3)],
                         [sum(r[i]*v for r, v in zip(rows, rhs)) for i in range(3)])
    except ValueError:
        return None
    cx, cy = ox+a/2, oy+b/2
    r2 = c+(a*a+b*b)/4
    if r2 <= 0:
        return None
    radius = math.sqrt(r2)
    error = max(abs(math.hypot(x-cx, y-cy)-radius) for x, y in points)
    if error > tolerance:
        if _linear_tolerance is None:
            # Rounded STL boolean-subdivision points need not lie exactly on
            # their chord. Retry within the user's fit budget, not a guessed
            # larger radial tolerance. Genuine polygon corners remain required.
            return fit_circle(original_points, tolerance, tolerance)
        return None
    angles = [math.atan2(y-cy, x-cx) for x, y in points]
    steps = [((b-a+math.pi) % (2*math.pi))-math.pi for a, b in zip(angles, angles[1:]+angles[:1])]
    if abs(abs(sum(steps))-2*math.pi) > 1e-5 or max(abs(v) for v in steps) > math.pi/6+1e-6:
        return None
    sagitta = radius*(1-math.cos(max(abs(v) for v in steps)/2))
    return {"center": (cx, cy), "radius": radius, "fit_error_mm": error,
            "source_contour_vertices": source_count, "circle_sample_vertices": len(points),
            "chord_deviation_mm": sagitta+error+linear_tolerance}


def cylinder_candidates(shape: Any, fit_tolerance: float) -> list[dict]:
    candidates = []
    for face in shape.Faces:
        if not isinstance(face.Surface, Part.Plane):
            continue
        axis = face.normalAt(0, 0)
        for component in (axis.x, axis.y, axis.z):
            if abs(component) > 1e-8:
                if component < 0:
                    axis = -axis
                break
        seed = App.Vector(1, 0, 0) if abs(axis.x) < .8 else App.Vector(0, 1, 0)
        u = axis.cross(seed)
        u.normalize()
        v = axis.cross(u)
        for wire in face.Wires:
            vertices = [p.Point for p in wire.OrderedVertexes]
            circle = fit_circle([(p.dot(u), p.dot(v)) for p in vertices], fit_tolerance)
            if circle is None:
                continue
            center = u*circle['center'][0]+v*circle['center'][1]
            radius = circle['radius']
            if any(abs(c['axis'].dot(axis)) > 1-1e-8 and
                   (c['center']-center).Length < fit_tolerance and
                   abs(c['radius']-radius) < fit_tolerance for c in candidates):
                continue
            sides, signs, heights, spans = [], [], [], []
            for side in shape.Faces:
                if not isinstance(side.Surface, Part.Plane) or abs(side.normalAt(0, 0).dot(axis)) > 1e-5:
                    continue
                pts = [p.Point for p in side.Vertexes]
                if not pts:
                    continue
                zs = [p.dot(axis) for p in pts]
                if max(zs)-min(zs) < fit_tolerance:
                    continue
                radial_values = [((p-center)-axis*p.dot(axis)).Length for p in pts]
                if any(r > radius+fit_tolerance or
                       r < radius-circle['chord_deviation_mm']-fit_tolerance for r in radial_values):
                    continue
                radial = side.CenterOfMass-center-axis*side.CenterOfMass.dot(axis)
                signs.append(side.normalAt(0, 0).dot(radial) > 0)
                sides.append(side)
                heights.extend(zs)
                spans.append((min(zs), max(zs)))
            if len(sides) < 12 or len(set(signs)) != 1:
                continue
            # Least-squares circles can pass through rounded STL vertices. A
            # tiny outward envelope avoids tangent slivers of the polygon wall
            # surviving boolean replacement. Account for this in the error budget.
            radii = [((p.Point-center)-axis*p.Point.dot(axis)).Length for s in sides for p in s.Vertexes]
            envelope = max(radii)+fit_tolerance*.1
            candidates.append({**circle, 'fitted_radius': radius, 'radius': envelope,
                               'chord_deviation_mm': circle['chord_deviation_mm']+abs(envelope-radius),
                               'center': center, 'axis': axis, 'u': u, 'v': v,
                               'ring': vertices, 'outer': signs[0],
                               'full_zmin': max(s[0] for s in spans),
                               'full_zmax': min(s[1] for s in spans),
                               'zmin': min(heights), 'zmax': max(heights), 'side_count': len(sides)})
    return candidates


def _restore_cylinder_pass(shape: Any, fit_tolerance: float, surface_tolerance: float,
                      checkpoint: Callable) -> tuple[Any, dict]:
    candidates = cylinder_candidates(shape, fit_tolerance)
    result = shape
    restored, skipped = [], []
    # Restore holes before outer bands so annular additions cannot fill holes.
    for index, c in enumerate(sorted(candidates, key=lambda c: c['outer'])):
        checkpoint('restoring_cylinder:%d' % index, .5)
        axis, center = c['axis'], c['center']
        entry = {'axis': list(axis), 'axis_point': list(center), 'radius_mm': c['radius'],
                 'fitted_radius_mm': c['fitted_radius'],
                 'source_contour_vertices': c['source_contour_vertices'],
                 'circle_sample_vertices': c['circle_sample_vertices'],
                 'axial_range_mm': [c['zmin'], c['zmax']], 'outer': c['outer'],
                 'fit_error_mm': c['fit_error_mm'], 'chord_deviation_mm': c['chord_deviation_mm']}
        if c['chord_deviation_mm'] > surface_tolerance:
            skipped.append({**entry, 'reason': 'Polygon-to-circle deviation exceeds surface tolerance'})
            continue
        base = center+axis*c['zmin']
        height = c['zmax']-c['zmin']
        if not c['outer']:
            # A cylindrical drill exiting an oblique plane extends farther
            # axially than the polygon's last chord. Extend only at proven
            # outward oblique exits, never through an axial blind-hole floor.
            extensions=[]
            for end,sign in [(c['zmin'],-1),(c['zmax'],1)]:
                extra=0.0
                for face in result.Faces:
                    if not isinstance(face.Surface,Part.Plane):
                        continue
                    dot=face.normalAt(0,0).dot(axis)*sign
                    if not .1 < dot < 1-1e-7:
                        continue
                    if any(abs(p.Point.dot(axis)-end)<=fit_tolerance and
                           abs(((p.Point-center)-axis*p.Point.dot(axis)).Length-c['fitted_radius'])
                           <=c['chord_deviation_mm']+fit_tolerance for p in face.Vertexes):
                        extra=max(extra,min(surface_tolerance,c['chord_deviation_mm']*math.sqrt(1-dot*dot)/dot+fit_tolerance))
                extensions.append(extra)
            base-=axis*extensions[0]
            height+=sum(extensions)
            entry['oblique_exit_extensions_mm']=extensions
        cylinder = Part.makeCylinder(c['radius'], height, base, axis)
        try:
            if c['outer']:
                if c['full_zmax']-c['full_zmin'] <= fit_tolerance:
                    raise ValueError('Outer band has a crossing opening or interrupted axial band; preserving original facets')
                # Only the proven common axial band may be replaced here.
                # Adding a full-circle skin over interrupted end layers creates
                # phantom walls where a rectangular cutout removed the circle.
                proposed = result
                # At a full-height circular band, replace a thin sleeve as a
                # whole. This removes sub-tolerance seams caused by rounded STL
                # triangles no longer exactly sharing the fitted polygon plane.
                lo, hi = c['full_zmin'], c['full_zmax']
                depth = max(4*fit_tolerance, 4*c['chord_deviation_mm'])
                if hi-lo > fit_tolerance and c['radius'] > 3*depth:
                    p = center+axis*lo
                    outer = Part.makeCylinder(c['radius']+depth, hi-lo, p, axis)
                    inner = Part.makeCylinder(c['radius']-depth, hi-lo, p, axis)
                    support = inner.cut(Part.makeCylinder(c['radius']-2*depth, hi-lo, p, axis))
                    # A crossing hole or another opening must not be filled.
                    if result.common(support).Volume < support.Volume*(1-1e-6):
                        raise ValueError('Outer band has a crossing opening; preserving original facets')
                    sleeve = Part.makeCylinder(c['radius'], hi-lo, p, axis).cut(inner)
                    proposed = proposed.cut(outer.cut(inner)).fuse(sleeve).removeSplitter()
            else:
                proposed = result.cut(cylinder).removeSplitter()
            if not proposed.isValid() or len(proposed.Solids) != len(shape.Solids):
                raise ValueError('Invalid solid after cylinder replacement')
            cylinders = [f for f in proposed.Faces if isinstance(f.Surface, Part.Cylinder)]
            if not any(abs(f.Surface.Radius-c['radius']) < fit_tolerance and
                       abs(f.Surface.Axis.dot(axis)) > 1-1e-7 and
                       ((f.Surface.Center-center)-axis*(f.Surface.Center-center).dot(axis)).Length < fit_tolerance
                       for f in cylinders):
                raise ValueError('No analytic cylinder in replacement result')
            result = proposed
            restored.append(entry)
        except Exception as exc:
            if getattr(exc,'code','')=='JOB_CANCELLED':
                raise
            skipped.append({**entry, 'reason': str(exc)})
    from .mesh_profiles import restore_extruded_profiles
    result, profiles = restore_extruded_profiles(result, fit_tolerance, surface_tolerance, checkpoint)
    return result, {'enabled': True, 'candidates': len(candidates), 'restored': restored,
                    'skipped': skipped, 'fit_tolerance_mm': fit_tolerance,
                    'surface_tolerance_mm': surface_tolerance,
                    'extruded_profiles': profiles,
                    'scope': 'Full cylinder bands and circular arcs in layered extrusions; other surfaces may remain faceted'}


def faceted_regions(shape: Any) -> list[dict]:
    """Find connected smooth-normal facet chains; absence is not a freeform proof."""
    from collections import defaultdict
    faces=shape.Faces
    edges=defaultdict(list)
    adjacency=defaultdict(set)
    for i,face in enumerate(faces):
        if not isinstance(face.Surface,Part.Plane):
            continue
        for edge in face.Edges:
            for j,other in edges[edge.hashCode()]:
                if not edge.isSame(other):
                    continue
                dot=face.normalAt(0,0).dot(faces[j].normalAt(0,0))
                if math.cos(math.radians(30.01))<=dot<=math.cos(math.radians(.05)):
                    adjacency[i].add(j);adjacency[j].add(i)
            edges[edge.hashCode()].append((i,edge))
    unseen=set(adjacency)
    regions=[]
    while unseen:
        pending=[unseen.pop()];component=set()
        while pending:
            i=pending.pop();component.add(i)
            following=adjacency[i]&unseen;unseen.difference_update(following);pending.extend(following)
        if len(component)>=4:
            regions.append({'faces':[i+1 for i in sorted(component)],'face_count':len(component),
                            'area_mm2':sum(faces[i].Area for i in component),
                            'reason':'Unresolved connected smooth-normal planar facet chain'})
    return regions


def restore_seeded_inner_walls(shape: Any, fit_tolerance: float, surface_tolerance: float,
                               checkpoint: Callable) -> tuple[Any, dict]:
    """Use recovered cylinders to finish connected truncated wall fragments.

    These may contain only one or two facets, insufficient to infer a circle
    independently. Never bridge separate axial components through a blind floor.
    """
    from collections import defaultdict
    result=shape
    restored,skipped=[],[]
    seeds=[]
    for f in shape.Faces:
        if not isinstance(f.Surface,Part.Cylinder):
            continue
        u0,u1,v0,v1=f.ParameterRange
        p=f.valueAt((u0+u1)/2,(v0+v1)/2)
        axis=f.Surface.Axis;center=f.Surface.Center-axis*f.Surface.Center.dot(axis)
        radial=p-center-axis*p.dot(axis)
        if f.normalAt((u0+u1)/2,(v0+v1)/2).dot(radial)>=0:
            continue
        if not any(abs(s[0].dot(axis))>1-1e-8 and (s[1]-center).Length<fit_tolerance
                   and abs(s[2]-f.Surface.Radius)<fit_tolerance for s in seeds):
            seeds.append((axis,center,f.Surface.Radius))
    for axis,center,radius in seeds:
        faces=result.Faces
        eligible={};analytic=set()
        for i,f in enumerate(faces):
            if isinstance(f.Surface,Part.Cylinder):
                c=f.Surface.Center-axis*f.Surface.Center.dot(axis)
                if abs(f.Surface.Axis.dot(axis))>1-1e-8 and (c-center).Length<fit_tolerance and abs(f.Surface.Radius-radius)<fit_tolerance:
                    u0,u1,v0,v1=f.ParameterRange
                    u,v=(u0+u1)/2,(v0+v1)/2
                    p=f.valueAt(u,v)
                    if f.normalAt(u,v).dot(p-center-axis*p.dot(axis))<0:
                        eligible[i]=f;analytic.add(i)
            elif isinstance(f.Surface,Part.Plane) and abs(f.normalAt(0,0).dot(axis))<1e-5:
                pts=[v.Point for v in f.Vertexes]
                rs=[((p-center)-axis*p.dot(axis)).Length for p in pts]
                radial=f.CenterOfMass-center-axis*f.CenterOfMass.dot(axis)
                if rs and max(rs)<=radius+fit_tolerance and min(rs)>=radius-surface_tolerance and f.normalAt(0,0).dot(radial)<0:
                    # The whole chord, not just its endpoints, must fit.
                    if abs(radial.Length-radius)<=surface_tolerance:
                        eligible[i]=f
        if len(eligible)==len(analytic):
            continue
        edges=defaultdict(list);adjacency=defaultdict(set)
        for i,f in eligible.items():
            for e in f.Edges:
                for j,other in edges[e.hashCode()]:
                    if e.isSame(other):adjacency[i].add(j);adjacency[j].add(i)
                edges[e.hashCode()].append((i,e))
        unseen=set(eligible)
        while unseen:
            pending=[unseen.pop()];component=set()
            while pending:
                i=pending.pop();component.add(i)
                following=adjacency[i]&unseen;unseen.difference_update(following);pending.extend(following)
            facets=component-analytic
            if not facets or not component.intersection(analytic):
                continue
            checkpoint('completing_seeded_cylinder',.76)
            pts=[v.Point for i in component for v in eligible[i].Vertexes]
            low,high=min(p.dot(axis) for p in pts),max(p.dot(axis) for p in pts)
            envelope=max(radius,max(((p-center)-axis*p.dot(axis)).Length for p in pts)+fit_tolerance*.1)
            entry={'axis':list(axis),'axis_point':list(center),'radius_mm':envelope,
                   'axial_range_mm':[low,high],'source_side_faces':len(facets)}
            try:
                proposed=result.cut(Part.makeCylinder(envelope,high-low,center+axis*low,axis)).removeSplitter()
                if not proposed.isValid() or len(proposed.Solids)!=len(shape.Solids):
                    raise ValueError('Invalid analytic-seeded completion')
                proposed.check(True)
                if len(proposed.Faces)>=len(result.Faces):
                    raise ValueError('No reduction of seeded wall fragments')
                result=proposed;restored.append(entry)
            except Exception as exc:
                if getattr(exc,'code','')=='JOB_CANCELLED':raise
                skipped.append({**entry,'reason':str(exc)})
    return result,{'restored':restored,'skipped':skipped}


def restore_cylinders(shape: Any, fit_tolerance: float, surface_tolerance: float,
                      checkpoint: Callable) -> tuple[Any, dict]:
    """Automatically rescan after each recovery pass; report unresolved faceting."""
    from .mesh_local_cylinders import restore_local_cylinders
    result=shape
    restored,skipped,local_restored,local_skipped,iterations=[],[],[],[],[]
    seeded_restored,seeded_skipped=[],[]
    profiles={'applied':False,'arcs':[]}
    initial=faceted_regions(shape)
    for index in range(4):
        checkpoint('automatic_surface_pass:%d'%index,.48)
        before=len(result.Faces)
        result,report=_restore_cylinder_pass(result,fit_tolerance,surface_tolerance,checkpoint)
        restored.extend(report['restored']);skipped.extend(report['skipped'])
        if report['extruded_profiles']['applied']:
            profiles=report['extruded_profiles']
        elif not profiles['applied']:
            profiles=report['extruded_profiles']
        result,local=restore_local_cylinders(result,fit_tolerance,surface_tolerance,checkpoint)
        local_restored.extend(local['restored']);local_skipped.extend(local['skipped'])
        result,seeded=restore_seeded_inner_walls(result,fit_tolerance,surface_tolerance,checkpoint)
        seeded_restored.extend(seeded['restored']);seeded_skipped.extend(seeded['skipped'])
        residual=faceted_regions(result)
        iterations.append({'pass':index+1,'faces_before':before,'faces_after':len(result.Faces),
                           'remaining_facet_chains':len(residual)})
        if len(result.Faces)>=before or not residual:
            break
    return result,{'enabled':True,'candidates':len(restored)+len(skipped),
        'restored':restored,'skipped':skipped,'extruded_profiles':profiles,
        'local_cylinders':{'restored':local_restored,'skipped':local_skipped},
        'analytic_seed_completion':{'restored':seeded_restored,'skipped':seeded_skipped},
        'fit_tolerance_mm':fit_tolerance,'surface_tolerance_mm':surface_tolerance,
        'automatic_passes':iterations,
        'faceting_check':{'initial_chains':len(initial),'remaining_regions':residual,
            'status':'residual_faceting' if residual else 'no_smooth_facet_chains_detected',
            'scope':'Connected >=4 planar faces, adjacent normal changes 0.05 to 30 degrees; not proof for arbitrary freeform geometry'},
        'scope':'Planar patches, full/local cylinders and layered arc profiles; automatic residual scan'}
