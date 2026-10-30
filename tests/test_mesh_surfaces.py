"""Geometry-only recognition tests; actual BREP replacement is tested live."""
import importlib.util
import math
from pathlib import Path
import sys
from types import ModuleType

import pytest


@pytest.fixture
def recognition(monkeypatch):
    monkeypatch.setitem(sys.modules, 'FreeCAD', ModuleType('FreeCAD'))
    monkeypatch.setitem(sys.modules, 'Part', ModuleType('Part'))
    path = Path(__file__).parents[1]/'addon/FreeCADMCP/freecad_mcp_core/mesh_surfaces.py'
    spec = importlib.util.spec_from_file_location('_test_mesh_surfaces', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ring(radius=21, count=72, cx=10, cy=0):
    return [(cx+radius*math.cos(2*math.pi*i/count), cy+radius*math.sin(2*math.pi*i/count))
            for i in range(count)]


def test_full_circle_fit_and_chord_deviation(recognition):
    fit = recognition.fit_circle(ring(), .001)
    assert fit['center'] == pytest.approx((10, 0))
    assert fit['radius'] == pytest.approx(21)
    assert fit['fit_error_mm'] < 1e-10
    assert fit['chord_deviation_mm'] == pytest.approx(21*(1-math.cos(math.pi/72))+.00001)


def test_circle_fit_is_translation_and_orientation_independent(recognition):
    fit = recognition.fit_circle(list(reversed(ring(cx=1e6, cy=-1e6))), .001)
    assert fit['center'] == pytest.approx((1e6, -1e6))
    assert fit['radius'] == pytest.approx(21)


@pytest.mark.parametrize('points', [ring(count=8), ring()[:40],
    [(x, y*1.1) for x, y in ring()], [(float(i), 0) for i in range(20)]])
def test_reject_coarse_polygon_partial_arc_ellipse_and_line(recognition, points):
    assert recognition.fit_circle(points, .001) is None


def test_small_mesh_rounding_accepted_but_distorted_vertex_rejected(recognition):
    points = [(round(x, 4), round(y, 4)) for x, y in ring()]
    assert recognition.fit_circle(points, .001)
    points[10] = (points[10][0]+.1, points[10][1])
    assert recognition.fit_circle(points, .001) is None


def test_rounded_subdivision_points_do_not_block_small_bores(recognition):
    points=ring(radius=1.2,cx=23,cy=6.5)
    contour=[]
    for i,p in enumerate(points):
        contour.append(tuple(round(x,3) for x in p))
        if i in (0,18,36,54):
            q=points[(i+1)%len(points)]
            contour.append(tuple(round((a+b)/2,3) for a,b in zip(p,q)))
    fit=recognition.fit_circle(contour,.001)
    assert fit and fit['radius']==pytest.approx(1.2,abs=.001)
    assert fit['source_contour_vertices']==76 and fit['circle_sample_vertices']>=12


@pytest.mark.parametrize('count,step,expected',[(4,5,1),(3,5,0),(5,45,0)])
def test_residual_scan_distinguishes_smooth_chains_from_sharp_edges(recognition,count,step,expected):
    from types import SimpleNamespace
    class Plane: pass
    recognition.Part.Plane=Plane
    class Normal:
        def __init__(self,angle): self.angle=angle
        def dot(self,other): return math.cos(math.radians(self.angle-other.angle))
    class Edge:
        def __init__(self,key): self.key=key
        def hashCode(self): return self.key
        def isSame(self,other): return self.key==other.key
    faces=[SimpleNamespace(Surface=Plane(),Area=1,Edges=[Edge(i),Edge(i+1)],
        normalAt=lambda u,v,i=i:Normal(i*step)) for i in range(count)]
    assert len(recognition.faceted_regions(SimpleNamespace(Faces=faces)))==expected


def test_round_bore_with_boolean_edge_subdivisions_is_recognized(recognition):
    original = ring(radius=4.85, cx=0, cy=11)
    contour = []
    for i, point in enumerate(original):
        contour.append(point)
        if i in (0, 18, 36, 54):
            next_point = original[(i+1) % len(original)]
            contour.append(tuple((a+b)/2 for a, b in zip(point, next_point)))
    assert len(contour) == 76
    fit = recognition.fit_circle(contour, .001)
    assert fit is not None
    assert fit['radius'] == pytest.approx(4.85)
    assert fit['source_contour_vertices'] == 76
    assert fit['circle_sample_vertices'] == 72


def test_subdividing_an_octagon_does_not_make_it_a_cylinder(recognition):
    corners = ring(count=8)
    subdivided = []
    for i, p in enumerate(corners):
        q = corners[(i+1) % len(corners)]
        subdivided.extend([(p[0]+(q[0]-p[0])*t/10, p[1]+(q[1]-p[1])*t/10) for t in range(10)])
    assert recognition.fit_circle(subdivided, .001) is None
