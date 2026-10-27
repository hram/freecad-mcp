"""Opt-in live RPC smoke: creates and closes ONLY a disposable document.

Run: uv run python scripts/smoke_mounting.py
Does not save files or change existing geometry. Restores previous active document.
"""
import json
import uuid
import sys
from freecad_mcp.freecad_client import FreeCADConnection
from freecad_mcp.assembly.bridge import build_script, MARKER

c = FreeCADConnection(host="127.0.0.1", port=9875)
name = "MCP_Mounting_Smoke_" + uuid.uuid4().hex[:8]


def execute(code):
    response = c.execute_code(code)
    if not response.get("success"):
        raise RuntimeError(response)
    return response["message"]


def call(op, **args):
    message = execute(build_script(op, args))
    assert MARKER in message, repr(message)
    result = json.loads(message.split(MARKER)[-1])
    return result


def ok(op, **args):
    r = call(op, **args)
    assert r["success"], r
    return r["result"]


previous = execute("print('__PREVIOUS__' + (App.ActiveDocument.Name if App.ActiveDocument else ''))").split('__PREVIOUS__')[-1].strip()
try:
    execute(f'''
import Part
_d = App.newDocument({name!r})
_parent = _d.addObject("App::Part", "Parent")
_parent.Placement = App.Placement(App.Vector(4,7,9), App.Rotation(App.Vector(0,0,1), 25))
_shape = Part.makeBox(20,20,4)
for _x in (5,15):
    _shape = _shape.cut(Part.makeCylinder(1.5,4,App.Vector(_x,5,0)))
_fixed = _d.addObject("Part::Feature", "Fixed")
_fixed.Shape = _shape
_fixed.ViewObject.ShapeColor = (0.3,0.6,0.9)
_parent.addObject(_fixed)
_moving_parent = _d.addObject("App::Part", "MovingParent")
_moving_parent.Placement = App.Placement(App.Vector(-3,11,5), App.Rotation(App.Vector(0,1,0), 31))
if {"--body" in sys.argv!r}:
    _moving = _d.addObject("PartDesign::Body", "Moving")
    _tip = _moving.newObject("PartDesign::Feature", "Tip")
    _tip.Shape = _shape.copy()
else:
    _moving = _d.addObject("Part::Feature", "Moving")
    _moving.Shape = _shape.copy()
_moving.ViewObject.ShapeColor = (0.9,0.5,0.2)
if hasattr(_moving, "Tip") and _moving.Tip:
    _moving.Tip.ViewObject.ShapeColor = (0.9,0.5,0.2)
_moving_parent.addObject(_moving)
_moving.Placement = App.Placement(App.Vector(50,30,12),App.Rotation(App.Vector(1,2,3),67))
_obstacle = _d.addObject("Part::Feature", "Obstacle")
_obstacle.Shape = Part.makeBox(200,200,200,App.Vector(-50,-50,-50))
_obstacle.Visibility = False
_d.recompute()
''')
    # Face indices are obtained from fresh source geometry, not hardcoded.
    raw = execute('''
print('__FACES__' + __import__('json').dumps({
 'top': next('Face%d'%i for i,f in enumerate(_shape.Faces,1) if isinstance(f.Surface,Part.Plane) and f.normalAt(0,0).z > .9),
 'bottom': next('Face%d'%i for i,f in enumerate(_shape.Faces,1) if isinstance(f.Surface,Part.Plane) and f.normalAt(0,0).z < -.9),
 'holes': ['Face%d'%i for i,f in enumerate(_shape.Faces,1) if isinstance(f.Surface,Part.Cylinder)]}))
''')
    faces = json.loads(raw.split('__FACES__')[-1])
    inspected = ok('inspect_part', doc_name=name, part='Fixed')
    assert len(inspected['cylinders']) == 2
    a = ok('define_interface', doc_name=name, part='Fixed', plane_face=faces['top'], hole_faces=faces['holes'])['interface_id']
    b = ok('define_interface', doc_name=name, part='Moving', plane_face=faces['bottom'], hole_faces=faces['holes'])['interface_id']
    p = ok('plan_alignment', fixed_interface=a, moving_interface=b, obstacles=['Obstacle'])['plan_id']
    before = ok('inspect_part', doc_name=name, part='Moving')['fingerprint']
    failed = call('apply_alignment', plan_id=p)
    assert not failed['success'] and 'Postcondition failed' in failed['error'], failed
    assert ok('inspect_part', doc_name=name, part='Moving')['fingerprint'] == before, 'Rollback changed placement'
    print('PASS collision failure rolls back placement')
    p = ok('plan_alignment', fixed_interface=a, moving_interface=b)['plan_id']
    report = ok('apply_alignment', plan_id=p)
    assert report['verification']['passed']
    assert ok('check_assembly', plan_id=p)['passed']
    images = ok('inspect_region', doc_name=name, parts=['Fixed', 'Moving'], size=512)
    assert len(images['views']) == 3
    import base64, struct
    for image in images['views']:
        png = base64.b64decode(image['png'])
        assert struct.unpack('>II', png[16:24]) == (512, 512)
    if '--image' in sys.argv:
        from pathlib import Path
        Path('/tmp/freecad-mounting-smoke.png').write_bytes(base64.b64decode(images['views'][0]['png']))
    print('PASS coherent Isometric/Top/Right PNG capture')
    assert not call('apply_alignment', plan_id=p)['success']
    assert not call('plan_alignment', fixed_interface=a, moving_interface=b)['success']
    print('PASS nested parent, rotated part, real hole/gap/collision checks, reuse and stale rejection')
    print(json.dumps(report, indent=2))
finally:
    execute(f"App.closeDocument({name!r})\n" + (f"App.setActiveDocument({previous!r})" if previous else ""))
    c.disconnect()
