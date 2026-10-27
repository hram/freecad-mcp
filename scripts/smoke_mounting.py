"""Opt-in live RPC smoke: creates and closes ONLY a disposable document.

Run: uv run python scripts/smoke_mounting.py
Does not save files or change existing geometry. Restores previous active document.
"""
import json
import uuid
import sys
import os
import time
from freecad_mcp.freecad_client import FreeCADConnection
from freecad_mcp.assembly.bridge import build_script, MARKER

c = FreeCADConnection(host="127.0.0.1", port=int(os.environ.get("FREECAD_MCP_PORT", "9875")))
name = "MCP_Mounting_Smoke_" + uuid.uuid4().hex[:8]


def execute(code):
    response = c.execute_code(code)
    if not response.get("success"):
        raise RuntimeError(response)
    return response["message"]


def call(op, **args):
    if '--native' in sys.argv:
        caps = c.assembly_capabilities()
        assert caps['assembly_protocol'] == 1
        submitted = c.assembly_submit(op, args, uuid.uuid4().hex)
        assert submitted['success'], submitted
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            job = c.assembly_job_status(submitted['job_id'])['job']
            if job['state'] not in ('queued', 'running'):
                assert job['state'] != 'outcome_unknown', job
                return job['result']
            time.sleep(.05)
        raise RuntimeError('Native job did not finish')
    message = execute(build_script(op, args))
    assert MARKER in message, repr(message)
    return json.loads(message.split(MARKER)[-1])


def ok(op, **args):
    r = call(op, **args)
    assert r["success"], r
    return r["result"]


previous = execute("print('__PREVIOUS__' + (App.ActiveDocument.Name if App.ActiveDocument else ''))").split('__PREVIOUS__')[-1].strip()
try:
    execute(f'''
import Part
_d = App.newDocument({name!r})
_assembly_mode = {"--assembly" in sys.argv!r}
_parent = None if _assembly_mode else _d.addObject("App::Part", "Parent")
if _parent:
    _parent.Placement = App.Placement(App.Vector(4,7,9), App.Rotation(App.Vector(0,0,1), 25))
_shape = Part.makeBox(20,20,4)
for _x in (5,15):
    _shape = _shape.cut(Part.makeCylinder(1.5,4,App.Vector(_x,5,0)))
_fixed = _d.addObject("Part::Feature", "Fixed")
_fixed.Shape = _shape
_fixed.ViewObject.ShapeColor = (0.3,0.6,0.9)
if _parent:
    _parent.addObject(_fixed)
_moving_parent = None if _assembly_mode else _d.addObject("App::Part", "MovingParent")
if _moving_parent:
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
if _moving_parent:
    _moving_parent.addObject(_moving)
_moving.Placement = App.Placement(App.Vector(50,30,12),App.Rotation(App.Vector(1,2,3),67))
_obstacle = _d.addObject("Part::Feature", "Obstacle")
_obstacle.Shape = Part.makeBox(200,200,200,App.Vector(-50,-50,-50))
_obstacle.Visibility = False
_d.recompute()
''')
    if '--assembly' in sys.argv:
        ok('create_assembly', doc_name=name, parts=['Fixed', 'Moving'])
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
    if '--native' in sys.argv:
        execute(f'''
from freecad_mcp_core import runtime as _smoke_runtime
from freecad_mcp_core.jobs import JobCancelled as _SmokeCancelled
def _smoke_cancel_after_move(stage, progress):
    if stage == 'verifying':
        raise _SmokeCancelled('integration cancellation after placement')
_smoke_cancelled = _smoke_runtime.dispatch('apply_alignment', {{'plan_id': {p!r}}}, checkpoint=_smoke_cancel_after_move)
assert not _smoke_cancelled['success'] and _smoke_cancelled['code'] == 'JOB_CANCELLED', _smoke_cancelled
''')
        assert ok('inspect_part', doc_name=name, part='Moving')['fingerprint'] == before
        print('PASS cancellation after movement rolls back real FreeCAD placement')
        p = ok('plan_alignment', fixed_interface=a, moving_interface=b)['plan_id']
        preview = ok('preview_alignment', plan_id=p, size=512)
        assert len(preview['views']) == 3
        if '--image' in sys.argv:
            import base64
            from pathlib import Path
            Path('/tmp/freecad-mounting-preview.png').write_bytes(base64.b64decode(preview['views'][0]['png']))
        print('PASS ghost placement preview and axis overlays')
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
    if '--assembly' in sys.argv:
        joint = ok('create_fixed_joint', plan_id=p, assembly_name='Assembly')
        assert joint['joint_type'] == 'Fixed'
        execute("_moving.Placement.Base = _moving.Placement.Base + App.Vector(7,3,1)\n_d.getObject('Assembly').solve()\n_d.recompute()")
        assert ok('check_assembly', plan_id=p)['passed']
        print('PASS native Fixed Joint solver restores mounting pose after deliberate displacement')
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory(prefix='mcp-assembly-roundtrip-') as folder:
            path = str(Path(folder) / (name + '.FCStd'))
            execute(f'''
_expected_relative = _fixed.getGlobalPlacement().inverse().multiply(_moving.getGlobalPlacement())
_d.saveAs({path!r})
App.closeDocument({name!r})
_d = App.openDocument({path!r})
_fixed, _moving = _d.getObject('Fixed'), _d.getObject('Moving')
assert _d.getObject({joint['joint']!r}).Proxy.__class__.__module__ == 'JointObject'
_moving.Placement.Base = _moving.Placement.Base + App.Vector(3,1,2)
_d.getObject('Assembly').solve()
_d.recompute()
assert _fixed.getGlobalPlacement().inverse().multiply(_moving.getGlobalPlacement()).isSame(_expected_relative,1e-6)
''')
            assert not call('check_assembly', plan_id=p)['success'], 'Plan must expire after close/reopen'
        print('PASS saved/reopened native joint solves; old document handles are invalidated')
    print(json.dumps(report, indent=2))
finally:
    execute(f"App.closeDocument({name!r})\n" + (f"App.setActiveDocument({previous!r})" if previous else ""))
    c.disconnect()
