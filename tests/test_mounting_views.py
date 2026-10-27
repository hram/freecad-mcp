"""Regression: selecting a Body must not hide its rendering Tip."""
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


@pytest.mark.parametrize('capture_fails', [False, True])
def test_body_tip_visible_during_capture_and_view_restored(monkeypatch, capture_fails):
    class Obj:
        def __init__(self, name, body=False):
            self.Name, self.Visibility, self.body = name, True, body
            self.Tip = None
        def isDerivedFrom(self, name):
            return self.body and name == 'PartDesign::Body'
        def getParentGeoFeatureGroup(self):
            return None
    body, tip, other = Obj('Body', True), Obj('Tip'), Obj('Other')
    body.Tip = tip
    objects = [body, tip, other]
    doc = SimpleNamespace(Name='D', Objects=objects, getObject=lambda name: next(o for o in objects if o.Name == name))
    app = ModuleType('FreeCAD')
    app.ActiveDocument, app.getDocument = doc, lambda name: doc
    monkeypatch.setitem(sys.modules, 'FreeCAD', app)
    monkeypatch.setitem(sys.modules, 'Part', ModuleType('Part'))
    seen, restored = [], []
    def save(path, *args):
        seen.append([o.Name for o in objects if o.Visibility])
        if capture_fails:
            raise RuntimeError('capture failed')
        Path(path).write_bytes(b'\x89PNG\r\n\x1a\n')
    view = SimpleNamespace(getCamera=lambda: 'original-camera', setCamera=restored.append,
                           saveImage=save, redraw=lambda: None, fitAll=lambda: None, viewTop=lambda: None)
    gui = ModuleType('FreeCADGui')
    gui.activeView = lambda: view
    monkeypatch.setitem(sys.modules, 'FreeCADGui', gui)
    dispatch = ModuleType('rpc_server.gui_dispatch')
    dispatch._flush_gui_events = lambda **kwargs: None
    monkeypatch.setitem(sys.modules, 'rpc_server.gui_dispatch', dispatch)
    source = Path(__file__).parents[1] / 'addon/FreeCADMCP/freecad_mcp_core/runtime.py'
    spec = importlib.util.spec_from_file_location('freecad_mcp_core._views_test', source)
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    runtime.fingerprint = lambda o: 'unchanged'
    # Verified Placement must not silently violate an existing native constraint network.
    group = SimpleNamespace(Group=[object()], isDerivedFrom=lambda name: name == 'Assembly::JointGroup')
    assembly = SimpleNamespace(Group=[group], isDerivedFrom=lambda name: name == 'Assembly::AssemblyObject',
                               getParentGeoFeatureGroup=lambda: None)
    constrained = SimpleNamespace(getParentGeoFeatureGroup=lambda: assembly)
    with pytest.raises(ValueError, match='existing joints'):
        runtime.require_unconstrained_owner(constrained)
    group.Group = []
    runtime.require_unconstrained_owner(constrained)
    if capture_fails:
        with pytest.raises(RuntimeError, match='capture failed'):
            runtime.inspect_region('D', ['Body'], ['Top'], 512)
    else:
        result = runtime.inspect_region('D', ['Body'], ['Top'], 512)
        assert len(result['views']) == 1
    assert seen == [['Body', 'Tip']]
    assert all(o.Visibility for o in objects)
    assert restored == ['original-camera']
