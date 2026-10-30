"""Live native/MCP smoke; only a disposable document is modified and closed.

Usage: uv run python scripts/smoke_mesh_conversion.py --stl /path/to/base.stl
Requires the current addon activated in an open FreeCAD with healthy RPC.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import uuid

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from freecad_mcp.freecad_client import FreeCADConnection


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stl', type=Path, required=True)
    parser.add_argument('--require-cylinders', action='store_true')
    parser.add_argument('--require-profile-arcs', action='store_true')
    parser.add_argument('--repair', action='store_true')
    parser.add_argument('--require-roundoff-repair', action='store_true')
    parser.add_argument('--require-planar-patches', action='store_true')
    parser.add_argument('--require-local-cylinders', action='store_true')
    parser.add_argument('--require-no-faceted-regions', action='store_true')
    args = parser.parse_args()
    c = FreeCADConnection(token=os.environ.get('FREECAD_MCP_TOKEN'))
    name = 'MeshConversionSmoke_' + uuid.uuid4().hex[:8]

    def execute(code: str) -> str:
        result = c.execute_code(code)
        assert result['success'], result
        return result['message']

    previous = {doc for doc in c.list_documents() if not doc.startswith('MeshConversionSmoke_')}
    active = execute("print('__ACTIVE__' + (App.ActiveDocument.Name if App.ActiveDocument else ''))").split('__ACTIVE__')[-1].strip()
    if active.startswith('MeshConversionSmoke_'):
        active = ''
    execute(f"App.newDocument({name!r})")
    try:
        params = StdioServerParameters(command=sys.executable,
            args=['-c', 'from freecad_mcp.server import main; main()', '--host', '127.0.0.1'])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = [t.name for t in (await session.list_tools()).tools]
                assert 'convert_mesh_to_brep' in names and len(names) == 32

                async def convert(**kwargs):
                    reply = await session.call_tool('convert_mesh_to_brep', {'doc_name': name, **kwargs})
                    wire = reply.model_dump(by_alias=True)
                    blocks = [b.text for b in reply.content if b.type == 'text']
                    assert not wire.get('isError'), blocks
                    data = json.loads(blocks[-1])
                    while 'job_id' in data:
                        await asyncio.sleep(.1)
                        reply = await session.call_tool('get_assembly_job', {'job_id': data['job_id']})
                        blocks = [b.text for b in reply.content if b.type == 'text']
                        state = json.loads(blocks[0])
                        if state['state'] in ('queued', 'running', 'outcome_unknown'):
                            continue
                        assert state['state'] == 'done', blocks
                        data = json.loads(blocks[-1])
                    return data

                if args.require_roundoff_repair:
                    failed = await session.call_tool('convert_mesh_to_brep',
                        {'doc_name': name, 'source_file': str(args.stl.resolve()), 'name': 'UnrepairedMustNotExist'})
                    assert failed.model_dump(by_alias=True).get('isError'), failed
                    assert 'self-intersections' in '\n'.join(b.text for b in failed.content if b.type == 'text')
                    execute("assert App.getDocument(%r).getObject('UnrepairedMustNotExist') is None" % name)
                report = await convert(source_file=str(args.stl.resolve()), name='BaseResult', repair=args.repair)
                assert report['valid'] and report['closed'] and report['solid_count'] == 1
                assert report['volume_change_percent'] <= .1
                if args.require_local_cylinders:
                    assert report['surface_recognition']['local_cylinders']['restored']
                if args.require_no_faceted_regions:
                    assert not report['surface_recognition']['faceting_check']['remaining_regions']
                if args.require_planar_patches:
                    planar = report['surface_recognition']['planar_patches']
                    assert planar['applied'] and planar['extended_brep_check_passed']
                    assert planar['faces_after'] < planar['faces_before']
                    assert planar['max_plane_deviation_mm'] <= .001
                if args.require_roundoff_repair:
                    snaps = [r for r in report['repairs'] if r['operation'] == 'bounded_decimal_grid_snap']
                    assert len(snaps) == 1 and snaps[0]['max_vertex_displacement_mm'] <= .001
                    assert not snaps[0]['self_intersections_after']
                    assert snaps[0]['topology_counts_preserved']
                if args.require_cylinders:
                    assert report['surface_types'].get('Cylinder', 0) > 0
                    assert report['surface_recognition']['restored']
                if args.require_profile_arcs:
                    assert report['surface_recognition']['extruded_profiles']['applied']
                    assert report['surface_recognition']['extruded_profiles']['arcs']
                print('PASS STL via actual stdio MCP:', json.dumps({k: report[k] for k in
                    ('face_count', 'surface_types', 'volume_change_percent', 'max_bounds_change_mm')}))
                execute(f'''
import Mesh, Part
_mc_doc = App.getDocument({name!r})
_mc_parent = _mc_doc.addObject('App::Part', 'Parent')
_mc_parent.Placement = App.Placement(App.Vector(20,30,40), App.Rotation(App.Vector(0,0,1), 23))
_mc_source = _mc_doc.addObject('Mesh::Feature', 'MovedMesh')
_mc_source.Mesh = Mesh.Mesh(Part.makeBox(3,4,5).tessellate(.1))
_mc_parent.addObject(_mc_source)
_mc_source.Placement = App.Placement(App.Vector(2,3,4), App.Rotation(App.Vector(1,0,0), 17))
_mc_before = _mc_source.Mesh.Topology
_mc_world = _mc_source.Mesh.copy()
_mc_world.Placement = App.Placement()
_mc_world.transform(_mc_source.getGlobalPlacement().toMatrix())
_mc_doc.recompute()
''')
                moved = await convert(mesh_object='MovedMesh', name='MovedResult', repair=True)
                assert moved['valid'] and moved['solid_count'] == 1
                execute('''
assert _mc_source.Mesh.Topology == _mc_before
assert _mc_source.Visibility
_mc_output = _mc_doc.getObject('MovedResult').Shape
_mc_expected = Part.makeBox(3,4,5)
_mc_expected.Placement = _mc_source.getGlobalPlacement()
assert _mc_output.cut(_mc_expected).Volume < 1e-6
assert _mc_expected.cut(_mc_output).Volume < 1e-6
for _mc_axis in ('XMin','YMin','ZMin','XMax','YMax','ZMax'):
    assert abs(getattr(_mc_output.BoundBox, _mc_axis)-getattr(_mc_world.BoundBox, _mc_axis)) < 1e-5
_mc_bad = _mc_doc.addObject('Mesh::Feature', 'OpenMesh')
_mc_bad.Mesh = Mesh.Mesh([(App.Vector(0,0,0),App.Vector(1,0,0),App.Vector(0,1,0))])
_mc_doc.recompute()
''')
                failed = await session.call_tool('convert_mesh_to_brep',
                    {'doc_name': name, 'mesh_object': 'OpenMesh', 'name': 'MustNotExist'})
                assert failed.model_dump(by_alias=True).get('isError'), failed
                execute("assert _mc_doc.getObject('MustNotExist') is None")
                print('PASS nested global placement, source preservation, conservative repair and open-mesh rejection')
                execute('''
_mc_pair = _mc_doc.addObject('Mesh::Feature', 'SeparateMeshes')
_mc_pair_mesh = Mesh.Mesh(Part.makeBox(2,2,2).tessellate(.1))
_mc_pair_mesh.addMesh(Mesh.Mesh(Part.makeBox(2,2,2,App.Vector(5,0,0)).tessellate(.1)))
_mc_pair.Mesh = _mc_pair_mesh
_mc_nested = _mc_doc.addObject('Mesh::Feature', 'NestedMeshes')
_mc_nested_mesh = Mesh.Mesh(Part.makeBox(10,10,10).tessellate(.1))
_mc_nested_mesh.addMesh(Mesh.Mesh(Part.makeBox(2,2,2,App.Vector(3,3,3)).tessellate(.1)))
_mc_nested.Mesh = _mc_nested_mesh
_mc_doc.recompute()
''')
                pair = await convert(mesh_object='SeparateMeshes', name='SeparateResult')
                assert pair['solid_count'] == 2 and abs(pair['volume_mm3']-16) < 1e-6
                nested = await session.call_tool('convert_mesh_to_brep',
                    {'doc_name': name, 'mesh_object': 'NestedMeshes', 'name': 'NestedResult'})
                assert nested.model_dump(by_alias=True).get('isError'), nested
                assert 'Nested/overlapping' in '\n'.join(b.text for b in nested.content if b.type == 'text')
                execute("assert _mc_doc.getObject('NestedResult') is None")
                print('PASS separate solids supported; nested shells rejected without filling a cavity')
                execute('''
_mc_tube = _mc_doc.addObject('Mesh::Feature', 'TubeMesh')
_mc_tube_shape = Part.makeCylinder(10,6).cut(Part.makeCylinder(4,6))
_mc_tube.Mesh = Mesh.Mesh(_mc_tube_shape.tessellate(.003))
_mc_tube.Placement = App.Placement(App.Vector(11,-5,9),App.Rotation(App.Vector(1,2,3),37))
_mc_tube_before = _mc_tube.Mesh.Topology
_mc_doc.recompute()
''')
                tube = await convert(mesh_object='TubeMesh', name='TubeResult', max_volume_change_percent=.5)
                assert tube['surface_types'].get('Cylinder') == 2, tube
                assert tube['face_count'] == 4, tube
                execute('''
_mc_tube_result = _mc_doc.getObject('TubeResult').Shape
_mc_tube_axis = _mc_tube.getGlobalPlacement().Rotation.multVec(App.Vector(0,0,1))
assert _mc_tube.Mesh.Topology == _mc_tube_before
_mc_radii = sorted(f.Surface.Radius for f in _mc_tube_result.Faces if isinstance(f.Surface,Part.Cylinder))
assert abs(_mc_radii[0]-4)<.001 and abs(_mc_radii[1]-10)<.001, _mc_radii
assert all(abs(f.Surface.Axis.dot(_mc_tube_axis))>1-1e-7 for f in _mc_tube_result.Faces if isinstance(f.Surface,Part.Cylinder))
''')
                print('PASS actual analytic inner/outer cylinders, four faces, arbitrary placement and source preservation')
                strict = await convert(mesh_object='TubeMesh', name='StrictTubeResult',
                    surface_tolerance_mm=1e-8, max_volume_change_percent=.5)
                assert not strict['surface_recognition']['restored']
                assert strict['surface_recognition']['skipped']
                assert strict['surface_types'].get('Cylinder', 0) == 0
                execute('''
_mc_cross = _mc_doc.addObject('Mesh::Feature', 'CrossDrilledTube')
# Fresh primitives avoid inheriting the tube's fine cached triangulation.
_mc_cross_shape = Part.makeCylinder(10,6).cut(Part.makeCylinder(4,6)).cut(Part.makeCylinder(1.5,30,App.Vector(-15,0,3),App.Vector(1,0,0)))
_mc_cross.Mesh = Mesh.Mesh(_mc_cross_shape.tessellate(.1))
_mc_doc.recompute()
''')
                cross = await convert(mesh_object='CrossDrilledTube', name='CrossDrilledResult',
                    max_volume_change_percent=1, surface_tolerance_mm=.2)
                assert any(s['outer'] and 'crossing opening' in s['reason']
                    for s in cross['surface_recognition']['skipped']), cross
                execute("assert not _mc_doc.getObject('CrossDrilledResult').Shape.isInside(App.Vector(9,0,3),1e-6,True)")
                print('PASS strict deviation limit rejects recognition; crossing drill hole remains open')
                execute('''
_mc_blind_bore = _mc_doc.addObject('Mesh::Feature','BlindBoreMesh')
_mc_blind_bore.Mesh = Mesh.Mesh(Part.makeBox(10,10,10).cut(
    Part.makeCylinder(2,5,App.Vector(5,5,5))).tessellate(.003))
_mc_triangle = Part.Face(Part.makePolygon([App.Vector(-6,-6,0),App.Vector(-6,6,0),
    App.Vector(-6,-6,12),App.Vector(-6,-6,0)])).extrude(App.Vector(12,0,0))
_mc_oblique_bore = _mc_doc.addObject('Mesh::Feature','ObliqueBoreMesh')
_mc_oblique_bore.Mesh = Mesh.Mesh(_mc_triangle.cut(
    Part.makeCylinder(1,14,App.Vector(0,-7,3),App.Vector(0,1,0))).tessellate(.003))
_mc_doc.recompute()
''')
                blind = await convert(mesh_object='BlindBoreMesh',name='BlindBoreResult')
                oblique = await convert(mesh_object='ObliqueBoreMesh',name='ObliqueBoreResult')
                assert blind['surface_types'].get('Cylinder') == 1
                assert oblique['surface_types'].get('Cylinder') == 1
                execute('''
_mc_blind_shape = _mc_doc.getObject('BlindBoreResult').Shape
assert _mc_blind_shape.isInside(App.Vector(5,5,4.99),1e-6,True), 'Blind bore floor drilled through'
assert not _mc_blind_shape.isInside(App.Vector(5,5,5.01),1e-6,True)
_mc_oblique_shape = _mc_doc.getObject('ObliqueBoreResult').Shape
assert _mc_oblique_shape.isValid()
assert not _mc_oblique_shape.isInside(App.Vector(0,2.99,3),1e-6,True), 'Oblique exit closed'
''')
                print('PASS automatic oblique bore exit and preserved axial blind-hole floor')
                execute('''
_mc_capsule = Part.makeBox(12,6,4,App.Vector(-6,-3,0))
for _mc_x in (-6,6):
    _mc_capsule = _mc_capsule.fuse(Part.makeCylinder(3,4,App.Vector(_mc_x,0,0)))
_mc_blind_slot = Part.makeBox(10,3,2,App.Vector(-5,-1.5,2))
for _mc_x in (-5,5):
    _mc_blind_slot = _mc_blind_slot.fuse(Part.makeCylinder(1.5,2,App.Vector(_mc_x,0,2)))
_mc_arc_source = _mc_doc.addObject('Mesh::Feature','BlindSlotMesh')
_mc_arc_source.Mesh = Mesh.Mesh(_mc_capsule.cut(_mc_blind_slot).removeSplitter().tessellate(.005))
_mc_arc_before = _mc_arc_source.Mesh.Topology
_mc_doc.recompute()
''')
                slot = await convert(mesh_object='BlindSlotMesh', name='BlindSlotResult', max_volume_change_percent=.5)
                assert slot['surface_recognition']['extruded_profiles']['applied'], slot
                assert slot['surface_types'].get('Cylinder') == 4, slot
                execute('''
_mc_arc_shape = _mc_doc.getObject('BlindSlotResult').Shape
assert _mc_arc_source.Mesh.Topology == _mc_arc_before
assert _mc_arc_shape.isInside(App.Vector(0,0,1),1e-6,True), 'Blind slot floor lost'
assert not _mc_arc_shape.isInside(App.Vector(0,0,3),1e-6,True), 'Blind slot filled'
assert sum(isinstance(f.Surface,Part.Plane) and abs(f.normalAt(0,0).y)>.9999 for f in _mc_arc_shape.Faces)==4, 'Straight walls lost'
_mc_arc_expected_volume = _mc_arc_shape.Volume
_mc_arc_expected_faces = len(_mc_arc_shape.Faces)
''')
                print('PASS partial arc surfaces, straight walls, blind slot depth and source preservation')
                import tempfile
                with tempfile.TemporaryDirectory(prefix='mesh-recovery-roundtrip-') as folder:
                    path = str(Path(folder)/(name+'.FCStd'))
                    execute(f'''
_mc_expected_volume = _mc_doc.getObject('TubeResult').Shape.Volume
_mc_expected_bounds = list(_mc_doc.getObject('TubeResult').Placement.toMatrix().A)
_mc_base_expected_volume = _mc_doc.getObject('BaseResult').Shape.Volume
_mc_base_expected_faces = len(_mc_doc.getObject('BaseResult').Shape.Faces)
_mc_base_expected_cylinders = sum(isinstance(f.Surface,Part.Cylinder) for f in _mc_doc.getObject('BaseResult').Shape.Faces)
_mc_doc.saveAs({path!r})
Gui.getDocument(_mc_doc.Name).save()
assert not Gui.getDocument(_mc_doc.Name).Modified, 'GUI remains modified after GUI save'
App.closeDocument(_mc_doc.Name)
_mc_doc = App.openDocument({path!r})
# Read the reopened result immediately: no Placement writes, solve or recompute.
_mc_base_reopened = _mc_doc.getObject('BaseResult').Shape
assert _mc_base_reopened.isValid() and _mc_base_reopened.isClosed()
assert abs(_mc_base_reopened.Volume-_mc_base_expected_volume)<1e-6
assert len(_mc_base_reopened.Faces)==_mc_base_expected_faces
assert sum(isinstance(f.Surface,Part.Cylinder) for f in _mc_base_reopened.Faces)==_mc_base_expected_cylinders
from freecad_mcp_core.mesh_surfaces import faceted_regions
assert not faceted_regions(_mc_base_reopened) if {args.require_no_faceted_regions!r} else True
_mc_reopened = _mc_doc.getObject('TubeResult')
assert _mc_reopened.Shape.isValid() and len(_mc_reopened.Shape.Solids)==1, 'Reopened solid invalid'
assert abs(_mc_reopened.Shape.Volume-_mc_expected_volume)<1e-6, 'Reopened volume changed'
assert max(abs(a-b) for a,b in zip(_mc_reopened.Placement.toMatrix().A,_mc_expected_bounds))<1e-12, 'Reopened placement changed'
assert len(_mc_reopened.Shape.Faces)==4, 'Reopened face count changed'
assert sum(isinstance(f.Surface,Part.Cylinder) for f in _mc_reopened.Shape.Faces)==2, 'Analytic cylinders lost on reopen'
_mc_arc_reopened = _mc_doc.getObject('BlindSlotResult').Shape
assert _mc_arc_reopened.isValid() and len(_mc_arc_reopened.Faces)==_mc_arc_expected_faces
assert abs(_mc_arc_reopened.Volume-_mc_arc_expected_volume)<1e-6
assert sum(isinstance(f.Surface,Part.Cylinder) for f in _mc_arc_reopened.Faces)==4
assert _mc_arc_reopened.isInside(App.Vector(0,0,1),1e-6,True)
assert not _mc_arc_reopened.isInside(App.Vector(0,0,3),1e-6,True)
''')
                print('PASS saved/reopened geometry checked before any movement or solver call')
    finally:
        execute(f"App.closeDocument({name!r})\n" +
                (f"if {active!r} in App.listDocuments(): App.setActiveDocument({active!r})" if active else ''))
        current = c.list_documents()
        assert set(previous).issubset(current), 'A pre-existing document was closed'
        assert name not in current, 'Disposable smoke document was left open'


if __name__ == '__main__':
    asyncio.run(main())
