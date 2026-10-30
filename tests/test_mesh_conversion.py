"""Conservative failures and transaction/cancellation boundaries for mesh conversion."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def conversion(monkeypatch, tmp_path):
    bounds = SimpleNamespace(XMin=0, YMin=0, ZMin=0, XMax=10, YMax=10, ZMax=10)
    mesh = SimpleNamespace(CountFacets=12, CountPoints=8, Volume=1000,
        BoundBox=bounds, Topology="triangles", hasNonManifolds=lambda: False,
        isSolid=lambda: True, hasSelfIntersections=lambda: False)
    solid = SimpleNamespace(Volume=1000, BoundBox=bounds, Solids=[object()],
        Faces=[SimpleNamespace(Surface=SimpleNamespace())],
        isValid=lambda: True, isClosed=lambda: True)
    solid.removeSplitter = lambda: solid
    shell = SimpleNamespace(isClosed=lambda: True)

    class Shape:
        Shells = [shell]

        def makeShapeFromMesh(self, topology, tolerance):
            assert topology == "triangles"

    events, objects = [], {}
    doc = SimpleNamespace(UndoMode=True, HasPendingTransaction=False,
        getObject=objects.get, recompute=lambda: None)

    def add_object(kind, name):
        result = SimpleNamespace(Name=name, addProperty=lambda *args: None)
        objects[name] = result
        return result

    doc.addObject = add_object
    doc.openTransaction = lambda name: events.append("open")
    doc.commitTransaction = lambda: events.append("commit")

    def abort():
        events.append("abort")
        objects.clear()

    doc.abortTransaction = abort
    app, part, meshes = ModuleType("FreeCAD"), ModuleType("Part"), ModuleType("Mesh")
    app.getDocument = lambda name: doc
    part.Shape, part.makeSolid = Shape, lambda value: solid
    meshes.Mesh = lambda path: mesh
    for name, module in [("FreeCAD", app), ("Part", part), ("Mesh", meshes)]:
        monkeypatch.setitem(sys.modules, name, module)
    path = Path(__file__).parents[1] / "addon/FreeCADMCP/freecad_mcp_core/runtime.py"
    spec = importlib.util.spec_from_file_location("freecad_mcp_core._mesh_test", path)
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    source = tmp_path / "source.stl"
    source.write_text("test stub")
    return SimpleNamespace(runtime=runtime, mesh=mesh, solid=solid, shell=shell,
        events=events, objects=objects, doc=doc, app=app,
        args={"doc_name": "D", "source_file": str(source), "recognize_surfaces": False})


def test_success_reports_measurements_and_preserves_source(conversion):
    c = conversion
    report = c.runtime.convert_mesh_to_brep(**c.args)
    assert report["solid_count"] == 1 and report["valid"] and report["closed"]
    assert report["volume_change_percent"] == 0
    assert not report["saved"] and not report["surface_recognition"]["enabled"]
    assert c.events == ["open", "commit"]
    assert list(c.objects) == ["MeshBREP"]
    assert c.mesh.CountFacets == 12


@pytest.mark.parametrize("field,value", [("tolerance_mm", 0), ("tolerance_mm", float("nan")),
    ("max_volume_change_percent", -1), ("repair", "yes"), ("name", "bad name")])
def test_bad_options_cannot_mutate_document(conversion, field, value):
    c = conversion
    with pytest.raises(ValueError):
        c.runtime.convert_mesh_to_brep(**c.args, **{field: value})
    assert c.events == [] and c.objects == {}


@pytest.mark.parametrize("defect", ["open", "nonmanifold", "selfintersection", "volume", "shell"])
def test_defects_fail_before_document_mutation(conversion, defect):
    c = conversion
    if defect == "open":
        c.mesh.isSolid = lambda: False
    elif defect == "nonmanifold":
        c.mesh.hasNonManifolds = lambda: True
    elif defect == "selfintersection":
        c.mesh.hasSelfIntersections = lambda: True
    elif defect == "volume":
        c.solid.Volume = 1100
    else:
        c.shell.isClosed = lambda: False
    with pytest.raises(ValueError):
        c.runtime.convert_mesh_to_brep(**c.args)
    assert c.events == [] and c.objects == {}


def test_ambiguous_source_and_existing_output_rejected(conversion):
    c = conversion
    with pytest.raises(ValueError, match="exactly one"):
        c.runtime.convert_mesh_to_brep(**c.args, mesh_object="Mesh")
    c.objects["MeshBREP"] = object()
    with pytest.raises(ValueError, match="already exists"):
        c.runtime.convert_mesh_to_brep(**c.args)
    assert c.events == []


def test_cancellation_rolls_back_output(conversion):
    from freecad_mcp_core.jobs import JobCancelled
    c = conversion

    def cancel(stage, progress):
        if stage == "committing":
            raise JobCancelled("cancel before commit")

    result = c.runtime.dispatch("convert_mesh_to_brep", c.args, checkpoint=cancel)
    assert not result["success"] and result["code"] == "JOB_CANCELLED"
    assert c.events == ["open", "abort"] and c.objects == {}


def test_mcp_forwards_native_operation(monkeypatch):
    from freecad_mcp import server
    from freecad_mcp.assembly import bridge
    seen = []
    monkeypatch.setattr(server, "get_freecad_connection", lambda: "connection")
    monkeypatch.setattr(bridge, "call", lambda *args: seen.append(args) or [])
    server.convert_mesh_to_brep(None, "D", mesh_object="Mesh", repair=True)
    connection, operation, args = seen[0]
    assert connection == "connection" and operation == "convert_mesh_to_brep"
    assert args["repair"] and args["mesh_object"] == "Mesh"


@pytest.fixture
def roundoff(conversion, monkeypatch):
    c = conversion
    meshes = sys.modules['Mesh']
    points = [(0.123456, 0, 0), (1, 0, 0), (0, 1, 0)]

    class SegmentEnd:
        def __sub__(self, other):
            return SimpleNamespace(Length=0.00016)

    c.mesh.Topology = (points, [(0, 1, 2)])
    c.mesh.CountPoints, c.mesh.CountFacets = 3, 1
    c.mesh.getSelfIntersections = lambda: [(0, 1, None, SegmentEnd())]
    c.candidates = []

    def construct(topology):
        assert isinstance(topology, list) and len(topology[0]) == 3
        indexed = (list(topology[0]), [(0, 1, 2)])
        candidate = SimpleNamespace(Topology=indexed, CountPoints=3,
            CountFacets=1, Volume=1000, hasNonManifolds=lambda: False,
            fixDegenerations=lambda tolerance: None,
            isSolid=lambda: True,
            hasSelfIntersections=lambda: indexed[0][0][0] != .123)
        c.candidates.append(candidate)
        return candidate

    monkeypatch.setattr(meshes, 'Mesh', construct)
    return c


def test_bounded_roundoff_keeps_source_and_reports_movement(roundoff):
    c = roundoff
    original = repr(c.mesh.Topology)
    repaired, report = c.runtime.repair_mesh_roundoff(c.mesh, .001, .1)
    assert repr(c.mesh.Topology) == original
    assert repaired is c.candidates[-1]
    assert report['grid_mm'] == .001
    assert report['changed_vertices'] == 1
    assert report['max_vertex_displacement_mm'] == pytest.approx(.000456)
    assert report['topology_counts_preserved']
    assert not report['self_intersections_after']


def test_roundoff_does_not_exceed_requested_precision(roundoff):
    with pytest.raises(ValueError, match='remain'):
        roundoff.runtime.repair_mesh_roundoff(roundoff.mesh, .0001, .1)


@pytest.mark.parametrize('field,value', [('CountPoints', 2), ('CountFacets', 0),
    ('Volume', 1100), ('hasNonManifolds', lambda: True),
    ('isSolid', lambda: False), ('hasSelfIntersections', lambda: True)])
def test_roundoff_rejects_topology_volume_and_validation_failures(roundoff, monkeypatch, field, value):
    c = roundoff
    meshes = sys.modules['Mesh']
    original = meshes.Mesh

    def construct(topology):
        result = original(topology)
        setattr(result, field, value)
        return result

    monkeypatch.setattr(meshes, 'Mesh', construct)
    with pytest.raises(ValueError, match='remain'):
        c.runtime.repair_mesh_roundoff(c.mesh, .001, .1)


def test_large_intersection_cannot_be_waived(roundoff):
    class End:
        def __sub__(self, other):
            return SimpleNamespace(Length=.01)

    roundoff.mesh.getSelfIntersections = lambda: [(0, 1, None, End())]
    with pytest.raises(ValueError, match='exceed'):
        roundoff.runtime.repair_mesh_roundoff(roundoff.mesh, 1, .1)
    assert not roundoff.candidates


def test_planar_edge_flip_preserves_boundary(conversion):
    a,b,c,d = (0,0,0),(1,0,0),(1,1,0),(0,1,0)
    report = conversion.runtime.validate_planar_retriangulation([(a,b,c),(a,c,d)], [(a,b,d),(b,c,d)])
    assert report == {'retriangulated_triangles': 2, 'planar_patches': 1}


@pytest.mark.parametrize('warp,reverse', [(True,False),(False,True)])
def test_nonplanar_or_reversed_edge_flip_rejected(conversion, warp, reverse):
    a,b,c,d = (0,0,0),(1,0,0),(1,1,.01 if warp else 0),(0,1,0)
    after = [(a,b,d),(b,c,d)]
    if reverse:
        after = [tuple(reversed(t)) for t in after]
    with pytest.raises(ValueError):
        conversion.runtime.validate_planar_retriangulation([(a,b,c),(a,c,d)], after)


def test_repaired_mesh_requires_extended_brep_check_before_mutation(conversion, monkeypatch):
    c = conversion
    c.mesh.hasSelfIntersections = lambda: True
    for name in ('removeDuplicatedPoints', 'removeDuplicatedFacets', 'harmonizeNormals'):
        setattr(c.mesh, name, lambda: None)
    monkeypatch.setattr(c.runtime, 'repair_mesh_roundoff', lambda *args:
        (c.mesh, {'operation': 'bounded_decimal_grid_snap'}))

    def invalid(check_geometry):
        assert check_geometry is True
        raise ValueError('BOP self-interference')

    c.solid.check = invalid
    with pytest.raises(ValueError, match='BOP self-interference'):
        c.runtime.convert_mesh_to_brep(**c.args, repair=True)
    assert c.events == [] and c.objects == {}
