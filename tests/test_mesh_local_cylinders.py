import importlib.util
import math
from pathlib import Path
import sys
from types import ModuleType

import pytest


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setitem(sys.modules,'FreeCAD',ModuleType('FreeCAD'))
    monkeypatch.setitem(sys.modules,'Part',ModuleType('Part'))
    root=Path(__file__).parents[1]/'addon/FreeCADMCP/freecad_mcp_core'
    for name in ('mesh_surfaces','mesh_profiles','mesh_local_cylinders'):
        spec=importlib.util.spec_from_file_location('freecad_mcp_core.'+name,root/(name+'.py'))
        module=importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules,spec.name,module)
        spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('angle,start,sweep,expected',[
    (0,350,30,True),(180,350,30,False),(5,10,-30,True),
    (355,10,-30,True),(40,10,-30,False),(-180,120,120,True)])
def test_arc_membership_wrap_and_direction(local,angle,start,sweep,expected):
    assert local.in_sweep(*map(math.radians,(angle,start,sweep)))==expected


def test_arc_margin_does_not_select_opposite_side(local):
    assert local.in_sweep(math.radians(-.1),0,math.pi/2,math.radians(.2))
    assert not local.in_sweep(math.pi,0,math.pi/2,math.radians(.2))
