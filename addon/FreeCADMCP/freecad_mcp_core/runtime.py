"""Executed on FreeCAD's GUI thread; no MCP/Python-environment dependencies.

Ephemeral handles intentionally expire after addon activation or FreeCAD restart.
No document properties, files, or topology are modified by inspection/planning.
"""
import hashlib
import json
import math
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from typing import Any

import FreeCAD as App
import Part
from . import geometry as g
from .revisions import RevisionTracker

INTERFACES = OrderedDict()
PLANS = OrderedDict()
LIMIT = 256
_tracker = None
_checkpoint_callback = None


def document_state(doc: Any) -> dict:
    global _tracker
    if _tracker is None:
        _tracker = RevisionTracker(App)
    return _tracker.snapshot(doc)


def checkpoint(stage: str, progress: float) -> None:
    if _checkpoint_callback is not None:
        _checkpoint_callback(stage, progress)



def remember(cache: OrderedDict, value: dict) -> str:
    key = uuid.uuid4().hex
    cache[key] = value
    while len(cache) > LIMIT:
        cache.popitem(last=False)
    return key


def xyz(v: Any) -> list[float]:
    return [v.x, v.y, v.z]


def vector(v: list[float]) -> Any:
    return App.Vector(*v)


def obj(doc_name: str, name: str) -> Any:
    document = App.getDocument(doc_name)
    result = document.getObject(name)
    if result is None:
        raise ValueError("Object not found: " + name)
    if result.isDerivedFrom("App::Link"):
        raise ValueError("App::Link is not supported for verified alignment yet; use the source part")
    return result


def parent_placement(o: Any) -> Any:
    return o.getGlobalPlacement().multiply(o.Placement.inverse())


def world_shape(o: Any) -> Any:
    if not hasattr(o, "Shape") or o.Shape.isNull():
        raise ValueError("Object has no shape: " + o.Name)
    result = o.Shape.copy()
    result.Placement = parent_placement(o).multiply(result.Placement)
    return result


def fingerprint(o: Any) -> str:
    # BREP includes topology and geometry; parent motion changes the world BREP too.
    return hashlib.sha256(world_shape(o).exportBrepToString().encode()).hexdigest()


def geometry_signature(o: Any) -> str:
    # BREP serialization embeds location caches even after resetting Placement.
    # Compare ordered local geometric invariants instead, quantized to 1e-7 mm.
    shape = o.Shape
    inverse = o.Placement.inverse()
    def rounded(values: list) -> list[float]:
        return [round(float(v), 7) + 0.0 for v in values]
    faces = []
    for f in shape.Faces:
        faces.append([type(f.Surface).__name__, rounded([f.Area]),
                      rounded(xyz(inverse.multVec(f.CenterOfMass))),
                      rounded([getattr(f.Surface, "Radius", 0)]), len(f.Edges)])
    vertices = [rounded(xyz(inverse.multVec(v.Point))) for v in shape.Vertexes]
    signature = [rounded([shape.Volume, shape.Area]), faces, vertices]
    return hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()


def placement_signature(o: Any) -> list[float]:
    return list(o.getGlobalPlacement().toMatrix().A)


def healthy(o: Any) -> Any:
    if o.State and any(str(s) in ("Invalid", "Error", "Touched") for s in o.State):
        raise ValueError("Recompute/fix object before use: " + o.Name)
    shape = world_shape(o)
    if not shape.isValid():
        raise ValueError("Invalid shape: " + o.Name)
    return shape


def face(shape: Any, name: str) -> Any:
    if not isinstance(name, str) or not name.startswith("Face") or not name[4:].isdigit():
        raise ValueError("Expected Face<number>, got " + str(name))
    index = int(name[4:]) - 1
    if index < 0 or index >= len(shape.Faces):
        raise ValueError("Face does not exist: " + name)
    return shape.Faces[index]


def center_normal(f: Any) -> tuple[Any, Any]:
    u0, u1, v0, v1 = f.ParameterRange
    return f.valueAt((u0+u1)/2, (v0+v1)/2), f.normalAt((u0+u1)/2, (v0+v1)/2)


def inspect_part(doc_name: str, part: str, feature_limit: int = 100,
                 plane_offset: int = 0, cylinder_offset: int = 0) -> dict:
    for value, name in ((feature_limit, "feature_limit"), (plane_offset, "plane_offset"), (cylinder_offset, "cylinder_offset")):
        if isinstance(value, bool) or not isinstance(value, int) or value < (1 if name == "feature_limit" else 0):
            raise ValueError("Invalid pagination: " + name)
    if feature_limit > 500:
        raise ValueError("feature_limit must be <=500")
    o = obj(doc_name, part)
    shape = healthy(o)
    planes, cylinders = [], []
    for i, f in enumerate(shape.Faces, 1):
        s = f.Surface
        p, n = center_normal(f)
        if isinstance(s, Part.Plane):
            planes.append({"face": "Face%d" % i, "point": xyz(p), "normal": xyz(n), "area_mm2": f.Area})
        elif isinstance(s, Part.Cylinder):
            axis = s.Axis
            radial = p - s.Center - axis * ((p-s.Center).dot(axis))
            inner = radial.dot(n) < 0
            cylinders.append({"face": "Face%d" % i, "axis_point": xyz(s.Center), "axis": xyz(axis),
                              "radius_mm": s.Radius, "inner_wall": inner,
                              "angular_span_rad": abs(f.ParameterRange[1]-f.ParameterRange[0]),
                              "classification": "candidate_hole" if inner else "outer_cylinder"})
    bb = shape.BoundBox
    return {"document": doc_name, "document_state": document_state(o.Document), "part": part, "fingerprint": fingerprint(o), "coordinates": "world",
            "units": "mm", "valid": True, "solid_count": len(shape.Solids), "volume_mm3": shape.Volume,
            "bounds": {"min": [bb.XMin, bb.YMin, bb.ZMin], "max": [bb.XMax, bb.YMax, bb.ZMax]},
            "planes": planes[plane_offset:plane_offset+feature_limit],
            "cylinders": cylinders[cylinder_offset:cylinder_offset+feature_limit],
            "feature_counts": {"planes": len(planes), "cylinders": len(cylinders)},
            "next_plane_offset": plane_offset+feature_limit if plane_offset+feature_limit < len(planes) else None,
            "next_cylinder_offset": cylinder_offset+feature_limit if cylinder_offset+feature_limit < len(cylinders) else None,
            "note": "Cylinders are candidates, not proof of through holes. Explicitly select mounting plane and ordered hole faces."}


def owner_of(geometry: Any, owner: str | None) -> Any:
    target = obj(geometry.Document.Name, owner or geometry.Name)
    allowed = ("Part::Feature", "PartDesign::Body", "App::Part")
    if not any(target.isDerivedFrom(t) for t in allowed):
        raise ValueError("Unsupported movable owner type: " + target.TypeId)
    if target != geometry and geometry not in target.OutListRecursive:
        raise ValueError("Geometry must belong to owner")
    # Do not move a feature inside a Body: move the Body itself.
    current = geometry.getParentGeoFeatureGroup()
    while current:
        if current.isDerivedFrom("PartDesign::Body") and target != current:
            raise ValueError("Use PartDesign Body as owner")
        current = current.getParentGeoFeatureGroup()
    return target


def define_interface(doc_name: str, part: str, plane_face: str, hole_faces: list[str],
                     owner: str | None = None) -> dict:
    o = obj(doc_name, part)
    shape = healthy(o)
    target = owner_of(o, owner)
    if len(hole_faces) < 2 or len(hole_faces) != len(set(hole_faces)):
        raise ValueError("Select at least two distinct ordered cylindrical hole faces")
    plane = face(shape, plane_face)
    if not isinstance(plane.Surface, Part.Plane):
        raise ValueError("Mounting face must be planar")
    origin, normal = center_normal(plane)
    points, radii, axes = [], [], []
    for name in hole_faces:
        f = face(shape, name)
        if not isinstance(f.Surface, Part.Cylinder):
            raise ValueError("Hole face must be cylindrical: " + name)
        s = f.Surface
        p, n = center_normal(f)
        radial = p - s.Center - s.Axis * ((p-s.Center).dot(s.Axis))
        if radial.dot(n) >= 0 or abs(abs(s.Axis.dot(normal))-1) > 1e-6:
            raise ValueError("Expected inner cylindrical wall perpendicular to mounting plane: " + name)
        if abs(f.ParameterRange[1]-f.ParameterRange[0]) < 2*math.pi-1e-5:
            raise ValueError("Partial cylinder is ambiguous as a mounting hole: " + name)
        hit = s.Center + s.Axis * ((origin-s.Center).dot(normal) / s.Axis.dot(normal))
        # Edge distance = zero for a real hole opening on this mounting face.
        if plane.distToShape(Part.Vertex(hit + radial))[0] > 1e-5:
            raise ValueError("Cylinder does not open onto selected mounting plane: " + name)
        points.append(xyz(hit))
        radii.append(s.Radius)
        axes.append(xyz(s.Axis))
    g.frame(points, xyz(normal))
    value = {"document": doc_name, "document_state": document_state(o.Document), "part": part, "owner": target.Name, "plane_face": plane_face,
             "hole_faces": hole_faces, "points": points, "normal": xyz(normal), "axes": axes, "radii_mm": radii,
             "fingerprint": fingerprint(o), "geometry_signature": geometry_signature(o), "owner_placement": placement_signature(target)}
    key = remember(INTERFACES, value)
    return {"interface_id": key, **value, "correspondence": "hole_faces order is explicit; symmetry is not guessed"}


def load_interface(key: str, check: bool = True) -> dict:
    if key not in INTERFACES:
        raise ValueError("Unknown/expired interface. Define it again.")
    i = INTERFACES[key]
    if document_state(App.getDocument(i["document"]))["token"] != i["document_state"]["token"]:
        raise ValueError("Document was closed/reopened. Define interface again.")
    if check:
        o = obj(i["document"], i["part"])
        owner = obj(i["document"], i["owner"])
        healthy(o)
        if fingerprint(o) != i["fingerprint"] or placement_signature(owner) != i["owner_placement"]:
            raise ValueError("Stale interface: geometry or placement changed. Inspect and define again.")
    return i


def independent(a: dict, b: dict) -> None:
    oa, ob = obj(a["document"], a["owner"]), obj(b["document"], b["owner"])
    if oa == ob or oa in ob.OutListRecursive or ob in oa.OutListRecursive:
        raise ValueError("Fixed and moving owners must be independent")


def plan_alignment(fixed_interface: str, moving_interface: str, gap_mm: float = 0, tolerance_mm: float = 0.01,
                   angular_tolerance_deg: float = 0.05, collision_tolerance_mm3: float = 0.001,
                   obstacles: list[str] | None = None) -> dict:
    a, b = load_interface(fixed_interface), load_interface(moving_interface)
    if a["document"] != b["document"]:
        raise ValueError("Both interfaces must be in the same document")
    independent(a, b)
    result = g.solve(a["points"], b["points"], a["normal"], b["normal"], gap_mm, tolerance_mm)
    result["angular_tolerance_deg"] = g.positive(angular_tolerance_deg, "angular_tolerance_deg")
    result["collision_tolerance_mm3"] = g.positive(collision_tolerance_mm3, "collision_tolerance_mm3", True)
    # Collision scope is explicit; never claim a whole-assembly check from two shapes.
    names = list(dict.fromkeys([a["part"], *(obstacles or [])]))
    moving = obj(b["document"], b["owner"])
    signatures = {}
    for name in names:
        obstacle = obj(a["document"], name)
        if obstacle == moving or obstacle in moving.OutListRecursive:
            raise ValueError("An obstacle belongs to the moving owner")
        healthy(obstacle)
        signatures[name] = fingerprint(obstacle)
    result.update(fixed_interface=fixed_interface, moving_interface=moving_interface, document=a["document"],
                  obstacles=names, obstacle_fingerprints=signatures, status="planned",
                  document_state=document_state(App.getDocument(a["document"])))
    key = remember(PLANS, result)
    return {"plan_id": key, **result, "mutated": False,
            "hole_pairs": list(zip(a["hole_faces"], b["hole_faces"])),
            "collision_scope": names, "note": "Rigid placement only; no persistent assembly constraints. Check occurs during apply."}


def matrix_placement(plan: dict) -> Any:
    matrix = App.Matrix()
    for row in range(3):
        for col in range(3):
            setattr(matrix, "A%d%d" % (row+1, col+1), plan["rotation"][row][col])
        setattr(matrix, "A%d4" % (row+1), plan["translation"][row])
    return App.Placement(matrix)


def measurements(plan: dict) -> dict:
    a, b = load_interface(plan["fixed_interface"], False), load_interface(plan["moving_interface"], False)
    fixed = obj(a["document"], a["part"])
    moving = obj(b["document"], b["part"])
    for interface, part in ((a, fixed), (b, moving)):
        if geometry_signature(part) != interface["geometry_signature"]:
            raise ValueError("Interface topology/geometry changed; define and plan again")
    fs, ms = healthy(fixed), healthy(moving)
    fp, fn = center_normal(face(fs, a["plane_face"]))
    mp, mn = center_normal(face(ms, b["plane_face"]))
    errors, angles = [], []
    for fa, fb in zip(a["hole_faces"], b["hole_faces"]):
        ca, cb = face(fs, fa).Surface, face(ms, fb).Surface
        hit_a = ca.Center + ca.Axis * ((fp-ca.Center).dot(fn) / ca.Axis.dot(fn))
        hit_b = cb.Center + cb.Axis * ((mp-cb.Center).dot(mn) / cb.Axis.dot(mn))
        errors.append((hit_b-hit_a-fn*plan["gap_mm"]).Length)
        angles.append(math.degrees(math.acos(min(1, max(-1, abs(ca.Axis.dot(cb.Axis)))))))
    face_angle = math.degrees(math.acos(min(1, max(-1, -fn.dot(mn)))))
    gap = (mp-fp).dot(fn)
    collisions = []
    for index, name in enumerate(plan["obstacles"]):
        checkpoint("checking_collision:" + name, 0.5 + 0.4*index/max(1,len(plan["obstacles"])))
        other = healthy(obj(a["document"], name))
        volume = ms.common(other).Volume
        collisions.append({"part": name, "overlap_mm3": volume})
        checkpoint("collision_checked:" + name, 0.5 + 0.4*(index+1)/max(1,len(plan["obstacles"])))
    ok = (max(errors) <= plan["tolerance_mm"] and abs(gap-plan["gap_mm"]) <= plan["tolerance_mm"]
          and max([face_angle, *angles]) <= plan["angular_tolerance_deg"]
          and all(c["overlap_mm3"] <= plan["collision_tolerance_mm3"] for c in collisions))
    return {"passed": ok, "hole_errors_mm": errors, "max_hole_error_mm": max(errors),
            "hole_axis_angles_deg": angles, "opposing_faces_error_deg": face_angle, "gap_mm": gap,
            "collisions": collisions, "collision_scope": plan["obstacles"],
            "moving_geometry_only": b["part"], "whole_assembly_checked": False}


def get_plan(plan_id: str) -> dict:
    if plan_id not in PLANS:
        raise ValueError("Unknown/expired plan. Plan again.")
    return PLANS[plan_id]


def require_unconstrained_owner(moving: Any) -> None:
    current = moving.getParentGeoFeatureGroup()
    while current is not None:
        if current.isDerivedFrom("Assembly::AssemblyObject"):
            groups = [o for o in current.Group if o.isDerivedFrom("Assembly::JointGroup")]
            if any(group.Group for group in groups):
                raise ValueError("Owner belongs to an Assembly with existing joints; use its constraint solver instead of Placement")
        current = current.getParentGeoFeatureGroup()


def apply_alignment(plan_id: str) -> dict:
    plan = get_plan(plan_id)
    if plan["status"] != "planned":
        raise ValueError("Plan has already been applied; use check_assembly or create a new plan")
    a, b = load_interface(plan["fixed_interface"]), load_interface(plan["moving_interface"])
    for name, signature in plan["obstacle_fingerprints"].items():
        if fingerprint(obj(a["document"], name)) != signature:
            raise ValueError("Obstacle changed after planning; plan again: " + name)
    moving = obj(b["document"], b["owner"])
    # A parent container with uninspected children cannot be verified as a single shape.
    if moving.Name != b["part"]:
        raise ValueError("Verified apply requires part=owner (inspect the Body itself; App::Part containers are inspection-only)")
    require_unconstrained_owner(moving)
    doc = moving.Document
    if document_state(doc) != plan["document_state"]:
        raise ValueError("Document changed after planning; plan again")
    checkpoint("validating", 0.15)
    if not doc.UndoMode:
        raise ValueError("Enable FreeCAD Undo before verified mutations")
    if doc.HasPendingTransaction:
        raise ValueError("Document already has an open transaction")
    before = App.Placement(moving.Placement)
    doc.openTransaction("MCP align mounting interfaces")
    try:
        checkpoint("moving", 0.25)
        delta = matrix_placement(plan)
        desired = delta.multiply(moving.getGlobalPlacement())
        moving.Placement = parent_placement(moving).inverse().multiply(desired)
        doc.recompute()
        checkpoint("verifying", 0.45)
        report = measurements(plan)
        if not report["passed"]:
            raise ValueError("Postcondition failed: " + json.dumps(report))
        checkpoint("committing", 0.95)
        doc.commitTransaction()
    except Exception:
        doc.abortTransaction()
        # Abort must restore placement even when FreeCAD undo recording is disabled.
        moving.Placement = before
        doc.recompute()
        raise
    plan["status"] = "applied"
    return {"plan_id": plan_id, "status": "applied", "verification": report,
            "saved": False, "undo_available": bool(doc.UndoMode), "undo": "MCP align mounting interfaces" if doc.UndoMode else None}


def check_assembly(plan_id: str) -> dict:
    plan = get_plan(plan_id)
    return {"plan_id": plan_id, "status": plan["status"], **measurements(plan)}


@contextmanager
def instant_view_changes():
    # Standard views are animated by default; a frame captured mid-animation has a skewed camera.
    params = App.ParamGet("User parameter:BaseApp/Preferences/View")
    previous = params.GetBool("UseNavigationAnimations")
    params.SetBool("UseNavigationAnimations", False)
    try:
        yield
    finally:
        params.SetBool("UseNavigationAnimations", previous)


def inspect_region(doc_name: str, parts: list[str], views: list[str] | None = None, size: int = 1024,
                   preview_plan: str | None = None) -> dict:
    """Coherent multi-view image set; camera and visibility restored even on failure."""
    import base64
    import os
    import tempfile
    import FreeCADGui as Gui
    from rpc_server.gui_dispatch import _flush_gui_events
    choices = {"Isometric": "viewIsometric", "Top": "viewTop", "Front": "viewFront",
               "Right": "viewRight", "Left": "viewLeft", "Back": "viewBack", "Bottom": "viewBottom"}
    views = ["Isometric", "Top", "Right"] if views is None else views
    if not views or len(views) > 6 or any(v not in choices for v in views):
        raise ValueError("Select 1..6 standard views")
    if isinstance(size, bool) or not isinstance(size, int) or size < 256 or size > 1536:
        raise ValueError("size must be an integer in 256..1536")
    if not parts or len(parts) > 20:
        raise ValueError("Select 1..20 parts")
    if App.ActiveDocument is None or App.ActiveDocument.Name != doc_name:
        raise ValueError("Bring the requested document's 3D view to the front first")
    doc = App.getDocument(doc_name)
    selected = [obj(doc_name, name) for name in parts]
    signatures = {o.Name: fingerprint(o) for o in selected}
    view = Gui.activeView()
    if view is None or not hasattr(view, "saveImage"):
        raise ValueError("Active tab must be a 3D view")
    camera = view.getCamera()
    visibility = [(o, o.Visibility) for o in doc.Objects if hasattr(o, "Visibility")]
    keep = set(selected)
    for o in selected:
        # A Body's view provider displays its Tip; hiding Tip hides the selected Body.
        if o.isDerivedFrom("PartDesign::Body") and o.Tip:
            keep.add(o.Tip)
        p = o.getParentGeoFeatureGroup()
        while p:
            keep.add(p)
            p = p.getParentGeoFeatureGroup()
    images = []
    overlay = None
    try:
        if preview_plan:
            overlay = alignment_overlay(get_plan(preview_plan))
            view.getSceneGraph().addChild(overlay)
        for o, _ in visibility:
            o.Visibility = o in keep
        with instant_view_changes():
            for index, name in enumerate(views):
                checkpoint("rendering:" + name, (index+1)/(len(views)+1))
                getattr(view, choices[name])()
                view.fitAll()
                _flush_gui_events(delay_ms=0)
                view.redraw()
                fd, path = tempfile.mkstemp(suffix=".png")
                os.close(fd)
                try:
                    try:
                        view.saveImage(path, size, size, "Current", "Framebuffer")
                    except TypeError:
                        view.saveImage(path, size, size, "Current")
                    with open(path, "rb") as f:
                        data = f.read()
                    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                        raise ValueError("FreeCAD failed to produce a PNG")
                    images.append({"view": name, "png": base64.b64encode(data).decode()})
                finally:
                    os.unlink(path)
    finally:
        if overlay is not None:
            view.getSceneGraph().removeChild(overlay)
        for o, visible in visibility:
            o.Visibility = visible
        view.setCamera(camera)
        view.redraw()
    if any(fingerprint(o) != signatures[o.Name] for o in selected):
        raise ValueError("Geometry changed while capturing views; inspect again")
    return {"document": doc_name, "parts": parts, "fingerprints": signatures, "views": images,
            "size": size, "preview_plan": preview_plan, "camera_and_visibility_restored": True,
            "note": "Visual evidence complements numeric checks; images alone do not prove alignment."}


def alignment_overlay(plan: dict) -> Any:
    """Temporary scene graph only: cyan target ghost, green fixed axes, orange moving axes."""
    from pivy import coin
    a, b = load_interface(plan["fixed_interface"]), load_interface(plan["moving_interface"])
    root = coin.SoSeparator()
    for interface, color in ((a, (0.2, 1, 0.2)), (b, (1, 0.5, 0.1))):
        lines = coin.SoSeparator()
        material = coin.SoBaseColor()
        material.rgb = color
        draw = coin.SoDrawStyle()
        draw.lineWidth = 3
        coords = coin.SoCoordinate3()
        points = []
        for p in interface["points"]:
            points.extend([g.sub(p, g.scale(interface["normal"], 8)), g.add(p, g.scale(interface["normal"], 8))])
        coords.point.setValues(0, len(points), points)
        segments = coin.SoLineSet()
        segments.numVertices.setValues(0, len(points)//2, [2]*(len(points)//2))
        for node in (material, draw, coords, segments):
            lines.addChild(node)
        root.addChild(lines)
    shape = world_shape(obj(b["document"], b["part"]))
    shape.Placement = matrix_placement(plan).multiply(shape.Placement)
    vertices, triangles = shape.tessellate(0.2)
    if len(triangles) > 200000:
        raise ValueError("Preview mesh is too large; inspect a smaller part")
    material = coin.SoMaterial()
    material.diffuseColor = (0.1, 0.8, 1)
    material.transparency = 0.45
    coords = coin.SoCoordinate3()
    coords.point.setValues(0, len(vertices), [xyz(v) for v in vertices])
    mesh = coin.SoIndexedFaceSet()
    indices = [i for triangle in triangles for i in (*triangle, -1)]
    mesh.coordIndex.setValues(0, len(indices), indices)
    for node in (material, coords, mesh):
        root.addChild(node)
    return root


def preview_alignment(plan_id: str, size: int = 1024) -> dict:
    plan = get_plan(plan_id)
    if plan["status"] != "planned":
        raise ValueError("Preview requires an unapplied plan")
    a, b = load_interface(plan["fixed_interface"]), load_interface(plan["moving_interface"])
    if document_state(App.getDocument(a["document"])) != plan["document_state"]:
        raise ValueError("Document changed after planning; plan again")
    return inspect_region(a["document"], list(dict.fromkeys([a["part"], b["part"], *plan["obstacles"]])),
                          size=size, preview_plan=plan_id)


def create_assembly(doc_name: str, parts: list[str], name: str = "Assembly") -> dict:
    doc = App.getDocument(doc_name)
    if not doc.UndoMode:
        raise ValueError("Enable FreeCAD Undo before verified mutations")
    if doc.HasPendingTransaction or not parts or len(parts) != len(set(parts)):
        raise ValueError("Expected distinct root parts and no open transaction")
    objects = [obj(doc_name, p) for p in parts]
    for o in objects:
        healthy(o)
        if o.getParentGeoFeatureGroup() is not None:
            raise ValueError("Only root parts may be moved into a new Assembly: " + o.Name)
    placements = {o.Name: App.Placement(o.Placement) for o in objects}
    doc.openTransaction("MCP create native Assembly")
    try:
        assembly = doc.addObject("Assembly::AssemblyObject", name)
        assembly.Type = "Assembly"
        assembly.newObject("Assembly::JointGroup", "Joints")
        for o in objects:
            assembly.addObject(o)
            o.Placement = placements[o.Name]
        doc.recompute()
        checkpoint("committing", 0.95)
        doc.commitTransaction()
    except Exception:
        doc.abortTransaction()
        raise
    return {"document": doc_name, "assembly": assembly.Name, "parts": parts, "saved": False}


def create_fixed_joint(plan_id: str, assembly_name: str, joint_name: str = "MountingJoint") -> dict:
    """Native FreeCAD fixed joint preserving the verified relative mounting pose."""
    import JointObject
    import UtilsAssembly
    plan = get_plan(plan_id)
    if plan["status"] != "applied" or not measurements(plan)["passed"]:
        raise ValueError("Apply and verify alignment before creating a persistent joint")
    a, b = load_interface(plan["fixed_interface"], False), load_interface(plan["moving_interface"], False)
    fixed, moving = obj(a["document"], a["owner"]), obj(b["document"], b["owner"])
    assembly = obj(a["document"], assembly_name)
    if not assembly.isDerivedFrom("Assembly::AssemblyObject"):
        raise ValueError("Expected native Assembly::AssemblyObject")
    if fixed.getParentGeoFeatureGroup() != assembly or moving.getParentGeoFeatureGroup() != assembly:
        raise ValueError("Both owners must be direct members of the supplied Assembly")
    # Keep the first implementation auditable: existing constraints can move other parts.
    group = UtilsAssembly.getJointGroup(assembly)
    if group.Group:
        raise ValueError("Assembly already has joints; extending an existing constraint network is unsupported")
    doc = fixed.Document
    if not doc.UndoMode:
        raise ValueError("Enable FreeCAD Undo before verified mutations")
    if doc.HasPendingTransaction:
        raise ValueError("Document already has an open transaction")
    old = {o.Name: App.Placement(o.Placement) for o in (fixed, moving)}
    doc.openTransaction("MCP create verified fixed joint")
    try:
        ground = group.newObject("App::FeaturePython", "GroundedMount")
        JointObject.GroundedJoint(ground, fixed)
        joint = group.newObject("App::FeaturePython", joint_name)
        JointObject.Joint(joint, 0)
        JointObject.ViewProviderJoint(joint.ViewObject)
        joint.Detach1, joint.Detach2 = True, True
        joint.Reference1, joint.Reference2 = (fixed, ["", ""]), (moving, ["", ""])
        joint.Placement1 = App.Placement()
        joint.Placement2 = moving.getGlobalPlacement().inverse().multiply(fixed.getGlobalPlacement())
        doc.recompute()
        checkpoint("solving_native_assembly", 0.4)
        assembly.solve()
        doc.recompute()
        report = measurements(plan)
        if not report["passed"]:
            raise ValueError("Native solver changed mounting pose: " + json.dumps(report))
        checkpoint("committing", 0.95)
        doc.commitTransaction()
    except Exception:
        doc.abortTransaction()
        for o in (fixed, moving):
            o.Placement = old[o.Name]
        doc.recompute()
        raise
    return {"assembly": assembly.Name, "joint": joint.Name, "joint_type": "Fixed",
            "grounded_part": fixed.Name, "verification": report, "saved": False,
            "note": "Native fixed joint preserves relative pose. Connector placements are detached from topology."}


def dispatch(operation: str, arguments: dict, checkpoint=None) -> dict:
    global _checkpoint_callback
    _checkpoint_callback = checkpoint
    handlers = {"inspect_part": inspect_part, "define_interface": define_interface, "plan_alignment": plan_alignment,
                "apply_alignment": apply_alignment, "check_assembly": check_assembly, "inspect_region": inspect_region,
                "preview_alignment": preview_alignment, "create_assembly": create_assembly, "create_fixed_joint": create_fixed_joint}
    try:
        result = handlers[operation](**arguments)
        return {"success": True, "result": result}
    except Exception as exc:
        return {"success": False, "error": type(exc).__name__ + ": " + str(exc), "code": getattr(exc, "code", "ASSEMBLY_FAILED")}
    finally:
        _checkpoint_callback = None
