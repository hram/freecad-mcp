"""Partial arc recognition, seams and straight profile preservation."""
import importlib.util
import math
from pathlib import Path
import sys
from types import ModuleType

import pytest


@pytest.fixture
def profiles(monkeypatch):
    monkeypatch.setitem(sys.modules, 'FreeCAD', ModuleType('FreeCAD'))
    monkeypatch.setitem(sys.modules, 'Part', ModuleType('Part'))
    root = Path(__file__).parents[1]/'addon/FreeCADMCP/freecad_mcp_core'
    for name in ('mesh_surfaces', 'mesh_profiles'):
        spec = importlib.util.spec_from_file_location('freecad_mcp_core.'+name, root/(name+'.py'))
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
    return module


def arc(radius=3, start=0, end=math.pi, count=19, cx=0, cy=0):
    return [(cx+radius*math.cos(start+(end-start)*i/(count-1)),
             cy+radius*math.sin(start+(end-start)*i/(count-1))) for i in range(count)]


def test_semicircle_fit_keeps_endpoints(profiles):
    points = arc()
    fitted = profiles.fit_arc(points, .001)
    assert fitted['radius_mm'] == pytest.approx(3)
    assert fitted['angle_deg'] == pytest.approx(180)
    cx, cy = fitted['center']
    for x, y in (points[0], points[-1]):
        assert math.hypot(x-cx, y-cy) == pytest.approx(fitted['radius_mm'])


@pytest.mark.parametrize('offset,reverse', [(0,False), (7,False), (15,True)])
def test_slot_has_two_arc_ends_and_keeps_straights(profiles, offset, reverse):
    points = arc(radius=2, start=-math.pi/2, end=math.pi/2, cx=10)
    points += arc(radius=2, start=math.pi/2, end=3*math.pi/2, cx=0)
    points = points[offset:]+points[:offset]
    if reverse:
        points.reverse()
    found = profiles.find_arcs(points, .001)
    assert len(found) == 2
    assert sorted(a['radius_mm'] for a in found) == pytest.approx([2,2])
    assert all(a['angle_deg'] == pytest.approx(180) for a in found)
    assert sum(a['edges'] for a in found) == len(points)-2


@pytest.mark.parametrize('points', [[(i,0) for i in range(20)],
    arc(count=4), arc(end=math.pi/12), arc(end=math.pi, count=5),
    [(x,y*1.2) for x,y in arc(count=37)]])
def test_rejects_lines_coarse_arcs_short_spans_and_ellipse(profiles, points):
    assert profiles.fit_arc(points, .001) is None


def test_full_circle_is_not_split_into_partial_arcs(profiles):
    assert profiles.find_arcs(arc(end=2*math.pi, count=73)[:-1], .001) == []


def test_fine_arc_grows_past_short_seed(profiles):
    points = arc(radius=2, count=181)
    points += [(p[0], p[1]-5) for p in [points[-1], points[0]]]
    found = profiles.find_arcs(points, .001)
    assert len(found) == 1
    assert found[0]['angle_deg'] == pytest.approx(180)
