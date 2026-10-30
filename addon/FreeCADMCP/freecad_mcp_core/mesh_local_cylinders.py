"""Restore local cylindrical bands without assuming a globally extruded part."""
import math
from typing import Any, Callable

import FreeCAD as App
import Part
from .mesh_profiles import find_arcs
from .mesh_surfaces import simplify_contour


def in_sweep(angle: float, start: float, sweep: float, margin: float = 0) -> bool:
    position = ((angle-start)*(1 if sweep > 0 else -1)) % (2*math.pi)
    return position <= abs(sweep)+margin or position >= 2*math.pi-margin


def local_candidates(shape: Any, fit_tolerance: float) -> list[dict]:
    candidates = []
    planes = [f for f in shape.Faces if isinstance(f.Surface, Part.Plane)]
    for cap in planes:
        axis = cap.normalAt(0,0)
        for component in axis:
            if abs(component) > 1e-8:
                if component < 0:
                    axis = -axis
                break
        seed = App.Vector(1,0,0) if abs(axis.x) < .8 else App.Vector(0,1,0)
        u = axis.cross(seed); u.normalize()
        v = axis.cross(u)
        for wire in cap.Wires:
            if any(not isinstance(e.Curve, Part.Line) for e in wire.Edges):
                continue
            points = simplify_contour([(p.Point.dot(u),p.Point.dot(v)) for p in wire.OrderedVertexes],
                                      min(fit_tolerance*.01,1e-5))
            for arc in find_arcs(points, fit_tolerance):
                cx,cy = arc['center']
                center = u*cx+v*cy
                radius = arc['radius_mm']
                chain = [points[(arc['start']+i)%len(points)] for i in range(arc['edges']+1)]
                angles = [math.atan2(y-cy,x-cx) for x,y in chain]
                sweep = sum((b-a+math.pi)%(2*math.pi)-math.pi for a,b in zip(angles,angles[1:]))
                step = max(abs((b-a+math.pi)%(2*math.pi)-math.pi) for a,b in zip(angles,angles[1:]))
                sides, spans, signs, radii = [], [], [], []
                for face in planes:
                    if abs(face.normalAt(0,0).dot(axis)) > 1e-5:
                        continue
                    pts = [p.Point for p in face.Vertexes]
                    heights = [p.dot(axis) for p in pts]
                    if max(heights)-min(heights) <= fit_tolerance:
                        continue
                    rs = [((p-center)-axis*p.dot(axis)).Length for p in pts]
                    if any(r > radius+fit_tolerance or r < radius-arc['chord_deviation_mm']-fit_tolerance for r in rs):
                        continue
                    if any(not in_sweep(math.atan2(p.dot(v)-cy,p.dot(u)-cx),angles[0],sweep,
                                        step*1.1+fit_tolerance/radius) for p in pts):
                        continue
                    radial = face.CenterOfMass-center-axis*face.CenterOfMass.dot(axis)
                    signs.append(face.normalAt(0,0).dot(radial)>0)
                    sides.append(face); spans.append((min(heights),max(heights))); radii.extend(rs)
                if len(sides)<4 or len(set(signs))!=1:
                    continue
                low,high = max(a for a,b in spans),min(b for a,b in spans)
                if high-low<=fit_tolerance:
                    continue
                if any(abs(c['axis'].dot(axis))>1-1e-8 and (c['center']-center).Length<fit_tolerance
                       and abs(c['fitted_radius']-radius)<fit_tolerance for c in candidates):
                    continue
                envelope = max(radii)+fit_tolerance*.1
                # Boolean-clipped end facets have endpoints on chords rather
                # than circles. Include those facets and trim the replacement
                # against their actual neighbouring planes, not radial guesses.
                clip_planes=[]
                for neighbour in planes:
                    if abs(neighbour.normalAt(0,0).dot(axis))>1e-5 or any(neighbour.isSame(f) for f in sides):
                        continue
                    if any(edge.isSame(other) for edge in neighbour.Edges for f in sides for other in f.Edges):
                        clip_planes.append((neighbour.CenterOfMass,neighbour.normalAt(0,0)))
                mid = angles[0]+sweep/2
                offsets=[(math.atan2(p.Point.dot(v)-cy,p.Point.dot(u)-cx)-mid+math.pi)%(2*math.pi)-math.pi
                         for f in sides for p in f.Vertexes]
                start = mid+min(offsets)-step
                expanded_sweep = max(offsets)-min(offsets)+2*step
                if expanded_sweep>=2*math.pi:
                    continue
                candidates.append({'axis':axis,'center':center,'u':u,'v':v,'start':angles[0],
                    'sweep':sweep,'radius':envelope,'fitted_radius':radius,'low':low,'high':high,
                    'outer':signs[0],'side_count':len(sides),'fit_error_mm':arc['fit_error_mm'],
                    'chord_deviation_mm':arc['chord_deviation_mm']+abs(envelope-radius),
                    'clip_planes':clip_planes,'sector_start':start,'sector_sweep':expanded_sweep,
                    'middle_angle':mid})
    return candidates


def ring_sector(c: dict, inner: float, outer: float) -> Any:
    def point(radius, angle):
        return c['center']+c['axis']*c['low']+radius*(c['u']*math.cos(angle)+c['v']*math.sin(angle))
    def raw(a,b):
        mid=(a+b)/2
        edges=[Part.Arc(point(outer,a),point(outer,mid),point(outer,b)).toShape(),
               Part.makeLine(point(outer,b),point(inner,b)),
               Part.Arc(point(inner,b),point(inner,mid),point(inner,a)).toShape(),
               Part.makeLine(point(inner,a),point(outer,a))]
        return Part.Face(Part.Wire(edges)).extrude(c['axis']*(c['high']-c['low']))

    central_start=c['middle_angle']-abs(c['sweep'])/2
    central_end=c['middle_angle']+abs(c['sweep'])/2
    result=raw(central_start,central_end)
    size=max(c['radius']*8,(c['high']-c['low'])*8)
    # A support plane can intersect the same circle twice. It must trim only
    # its neighbouring end flap, not remove the centre of an otherwise valid arc.
    for a,b,junction in [(c['sector_start'],central_start,central_start),
                         (central_end,c['sector_start']+c['sector_sweep'],central_end)]:
        if b-a<1e-9:
            continue
        flap=raw(a,b)
        witness=point(c['radius'],junction)+c['axis']*((c['high']-c['low'])/2)
        if c['clip_planes']:
            origin,normal=min(c['clip_planes'],key=lambda plane: abs((witness-plane[0]).dot(plane[1])))
            if (witness-origin).dot(normal)<0:
                normal=-normal
            seed=App.Vector(1,0,0) if abs(normal.x)<.8 else App.Vector(0,1,0)
            u=normal.cross(seed);u.normalize();v=normal.cross(u)
            corners=[origin+u*x*size+v*y*size for x,y in [(-1,-1),(1,-1),(1,1),(-1,1)]]
            halfspace=Part.Face(Part.makePolygon(corners+[corners[0]])).extrude(normal*size)
            flap=flap.common(halfspace)
        result=result.fuse(flap)
    return result.removeSplitter()


def restore_local_cylinders(shape: Any, fit_tolerance: float, surface_tolerance: float,
                            checkpoint: Callable, seed_candidates: list | None = None) -> tuple[Any, dict]:
    result, restored, skipped = shape, [], []
    candidates = local_candidates(shape,fit_tolerance) if seed_candidates is None else seed_candidates
    for c in candidates:
        checkpoint('restoring_local_cylinder',.72)
        # Adjacent axial bands belong to the same cylinder. Reuse a compatible
        # analytic support instead of creating a second almost-coaxial surface.
        for face in result.Faces:
            if not isinstance(face.Surface,Part.Cylinder):
                continue
            axis=c['axis'];surface=face.Surface
            center=surface.Center-axis*surface.Center.dot(axis)
            shift=(center-c['center']).Length
            if abs(surface.Axis.dot(axis))<1-1e-8 or shift>fit_tolerance or abs(surface.Radius-c['radius'])>fit_tolerance:
                continue
            u0,u1,v0,v1=face.ParameterRange
            p=face.valueAt((u0+u1)/2,(v0+v1)/2)
            radial=p-center-axis*p.dot(axis)
            outer=face.normalAt((u0+u1)/2,(v0+v1)/2).dot(radial)>0
            if outer!=c['outer']:
                continue
            c={**c,'center':center,'radius':surface.Radius,
                'chord_deviation_mm':c['chord_deviation_mm']+shift+abs(surface.Radius-c['radius']),
                'reused_analytic_support':True}
            break
        entry = {'axis':list(c['axis']),'axis_point':list(c['center']),
                 'radius_mm':c['radius'],'fitted_radius_mm':c['fitted_radius'],
                 'seed_sweep_deg':math.degrees(abs(c['sweep'])),'axial_range_mm':[c['low'],c['high']],
                 'outer':c['outer'],'source_side_faces':c['side_count'],
                 'fit_error_mm':c['fit_error_mm'],'chord_deviation_mm':c['chord_deviation_mm']}
        entry['trim_planes']=len(c['clip_planes'])
        entry['reused_analytic_support']=c.get('reused_analytic_support',False)
        try:
            if c['chord_deviation_mm']>surface_tolerance:
                raise ValueError('Local cylinder exceeds surface tolerance')
            depth=max(4*fit_tolerance,4*c['chord_deviation_mm'])
            if c['radius']<=3*depth:
                raise ValueError('Insufficient radial room for local-band replacement')
            if c['outer']:
                support=ring_sector(c,c['radius']-2*depth,c['radius']-depth)
                if result.common(support).Volume < support.Volume*(1-1e-6):
                    raise ValueError('Local band interrupted by a crossing hole or missing support')
                removal=ring_sector(c,c['radius']-depth,c['radius']+depth)
                sleeve=ring_sector(c,c['radius']-depth,c['radius'])
                proposed=result.cut(removal).fuse(sleeve).removeSplitter()
            else:
                proposed=result.cut(ring_sector(c,c['radius']-depth,c['radius'])).removeSplitter()
            if not proposed.isValid() or len(proposed.Solids)!=len(shape.Solids):
                raise ValueError('Invalid local-cylinder replacement')
            proposed.check(True)
            cylinders=[f for f in proposed.Faces if isinstance(f.Surface,Part.Cylinder)
                       and abs(f.Surface.Radius-c['radius'])<fit_tolerance
                       and abs(f.Surface.Axis.dot(c['axis']))>1-1e-7
                       and min(max(v.Point.dot(c['axis']) for v in f.Vertexes),c['high'])
                           -max(min(v.Point.dot(c['axis']) for v in f.Vertexes),c['low'])>fit_tolerance
                       and ((f.Surface.Center-c['center'])-c['axis']*(f.Surface.Center-c['center']).dot(c['axis'])).Length<fit_tolerance]
            if not cylinders:
                raise ValueError('No analytic local cylinder in replacement result')
            entry['sweep_deg']=sum(math.degrees(abs(f.ParameterRange[1]-f.ParameterRange[0])) for f in cylinders)
            entry['angular_spans_deg']=[math.degrees(abs(f.ParameterRange[1]-f.ParameterRange[0])) for f in cylinders]
            if len(proposed.Faces)>=len(result.Faces):
                raise ValueError('No reduction of faceting')
            result=proposed;restored.append(entry)
        except Exception as exc:
            if getattr(exc,'code','')=='JOB_CANCELLED':
                raise
            skipped.append({**entry,'reason':str(exc)})
    return result,{'candidates':len(candidates),'restored':restored,'skipped':skipped,
                   'scope':'Local arc bands with a common axial span and verified material support'}
