"""Near-plane grouping must not grow across bends, parallel walls or holes."""
import importlib.util
import math
from pathlib import Path
import sys
from types import ModuleType

import pytest


@pytest.fixture
def planes(monkeypatch):
    monkeypatch.setitem(sys.modules, 'FreeCAD', ModuleType('FreeCAD'))
    monkeypatch.setitem(sys.modules, 'Part', ModuleType('Part'))
    path = Path(__file__).parents[1]/'addon/FreeCADMCP/freecad_mcp_core/mesh_planes.py'
    spec = importlib.util.spec_from_file_location('_test_mesh_planes', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def patch(z=0, area=1, normal=(0,0,1)):
    return {'area':area, 'normal':normal, 'origin':(0,0,z),
            'points':[(0,0,z),(1,0,z),(0,1,z)]}


def test_noisy_plane_group_uses_largest_fixed_support(planes):
    samples = [patch(.00000017),patch(0,area=20),patch(-.0000001)]
    group, = planes.group_plane_samples(samples,.001)
    assert group['members']==[0,1,2]
    assert group['origin']==(0,0,0)
    assert group['max_plane_deviation_mm']==pytest.approx(.00000017)


def test_every_vertex_must_fit_not_only_the_centroid(planes):
    bent = patch();bent['points'][2]=(0,1,.002)
    assert planes.group_plane_samples([patch(area=10),bent],.001)==[]


def test_no_cumulative_plane_drift(planes):
    groups = planes.group_plane_samples([patch(0,area=10),patch(.0009),patch(.0018)],.001)
    assert groups[0]['members']==[0,1]


@pytest.mark.parametrize('other', [patch(.01),patch(normal=(0,0,-1)),
    patch(normal=(math.sin(.01),0,math.cos(.01)))])
def test_separate_parallel_opposed_and_bent_planes_stay_separate(planes, other):
    assert planes.group_plane_samples([patch(area=10),other],.001)==[]


def test_oblique_plane_not_axis_specific(planes):
    n=(0,1/math.sqrt(2),1/math.sqrt(2))
    a={'area':20,'normal':n,'origin':(0,0,0),'points':[(0,0,0),(1,0,0),(0,1,-1)]}
    b={'area':1,'normal':n,'origin':(2,0,0),'points':[(2,0,0),(3,0,0),(2,1,-1+.0000002)]}
    group,=planes.group_plane_samples([a,b],.001)
    assert group['max_plane_deviation_mm']==pytest.approx(.0000002/math.sqrt(2))
