"""Unify near-planar BREP patches without replacing their trim boundaries."""
import math
from typing import Any, Callable

import FreeCAD as App
import Part


def group_plane_samples(samples: list[dict], tolerance: float) -> list[dict]:
    """Use fixed largest-face planes: never accumulate drift by region growing."""
    available = set(range(len(samples)))
    groups = []
    for seed in sorted(available, key=lambda i: (-samples[i]['area'], i)):
        if seed not in available:
            continue
        base = samples[seed]
        length = math.sqrt(sum(x*x for x in base['normal']))
        normal = tuple(x/length for x in base['normal'])
        members, max_drift = [], 0.0
        for index in sorted(available):
            sample = samples[index]
            other_length = math.sqrt(sum(x*x for x in sample['normal']))
            dot = sum(a*b for a,b in zip(normal, sample['normal']))/other_length
            if dot < 1-1e-7:  # Do not merge opposed walls or cylindrical facets.
                continue
            drift = max(abs(sum((p[j]-base['origin'][j])*normal[j] for j in range(3)))
                        for p in sample['points'])
            if drift <= tolerance:
                members.append(index)
                max_drift = max(max_drift, drift)
        available.difference_update(members)
        if len(members) > 1:
            groups.append({'members': members, 'normal': normal, 'origin': base['origin'],
                           'max_plane_deviation_mm': max_drift})
    return groups


def restore_planes(shape: Any, tolerance: float, checkpoint: Callable) -> tuple[Any, dict]:
    """Re-support near-coplanar faces on shared planes, then unify same-domain faces.

    Original wires (including holes and curved edges) remain trim boundaries.
    This is tolerance-bounded BREP reconstruction, not rectangle inference.
    """
    report = {'applied': False, 'patches': [], 'fit_tolerance_mm': tolerance,
              'coordinate_frame': 'source_local',
              'scope': 'Near-coplanar faces with matching normal directions; existing trim wires retained'}
    solids, all_patches = [], []
    try:
        # fixTolerance/fix can modify edge TShapes shared by wires. Work on a
        # deep copy so even failed reconstructions leave the input untouched.
        working = shape.copy()
        for source in working.Solids:
            faces = source.Faces
            indices = [i for i,f in enumerate(faces) if isinstance(f.Surface, Part.Plane)]
            samples = [{'area': faces[i].Area, 'normal': tuple(faces[i].normalAt(0,0)),
                        'origin': tuple(faces[i].CenterOfMass),
                        'points': [tuple(v.Point) for v in faces[i].Vertexes]} for i in indices]
            groups = group_plane_samples(samples, tolerance)
            replacements, patches = {}, []
            for group in groups:
                checkpoint('reconstructing_planar_patch', .46)
                plane = Part.Plane(App.Vector(*group['origin']), App.Vector(*group['normal']))
                native_tolerance = max(1e-7, min(tolerance, group['max_plane_deviation_mm']*1.1+1e-7))
                for member in group['members']:
                    i = indices[member]
                    original = faces[i]
                    rebuilt = Part.Face(plane, original.Wires)
                    rebuilt.fixTolerance(native_tolerance)
                    rebuilt.fix(native_tolerance, 1e-7, native_tolerance)
                    if rebuilt.normalAt(0,0).dot(original.normalAt(0,0)) < 0:
                        rebuilt.reverse()
                    if not rebuilt.isValid():
                        raise ValueError('Invalid planar trim reconstruction')
                    replacements[i] = rebuilt
                patches.append({k:v for k,v in group.items() if k != 'members'} |
                               {'input_faces': len(group['members']), 'brep_tolerance_mm': native_tolerance})
            if not replacements:
                solids.append(source)
                continue
            shell = Part.makeShell([replacements.get(i,f) for i,f in enumerate(faces)])
            shell.sewShape()
            if not shell.isClosed() or len(shell.Shells) != 1:
                raise ValueError('Planar reconstruction did not preserve a closed shell')
            proposed = Part.makeSolid(shell).removeSplitter()
            if proposed.Volume < 0:
                proposed.reverse()
            if not proposed.isValid() or not proposed.isClosed() or len(proposed.Solids) != 1:
                raise ValueError('Invalid solid after planar reconstruction')
            proposed.check(True)
            if len(proposed.Faces) >= len(source.Faces):
                solids.append(source)
                continue
            solids.append(proposed)
            all_patches.extend(patches)
        if not all_patches:
            return shape, report
        result = solids[0] if len(solids)==1 else Part.makeCompound(solids)
        result.check(True)
        return result, {**report, 'applied': True, 'patches': all_patches,
            'faces_before': len(shape.Faces), 'faces_after': len(result.Faces),
            'max_plane_deviation_mm': max(p['max_plane_deviation_mm'] for p in all_patches),
            'extended_brep_check_passed': True}
    except Exception as exc:
        if getattr(exc, 'code', '') == 'JOB_CANCELLED':
            raise
        return shape, {**report, 'reason': str(exc)}
