# Automatic STL-to-BREP reconstruction

`convert_mesh_to_brep` turns an STL file or a FreeCAD mesh into a separate
BREP solid, recovering supported planes and cylindrical surfaces from the
geometry. It does not need OpenSCAD sources, part names, a reviewed dimensions
list, or manually selected faces.

STL stores triangles, not circles or cylinders. Ordinary sewing can produce a
valid solid whose round holes still have dozens of flat walls. This tool goes
further: it replaces supported faceted patches with actual analytic surfaces.
That is a geometry change, not a display-smoothing setting.

| Stage | Result | What it does not establish |
| --- | --- | --- |
| Sew mesh into BREP | Closed, valid solids with explicit faces and edges | Round features may still be polygonal |
| Recognize surfaces | Actual planes and full or partial cylindrical surfaces | Not the original design history or exact intended dimensions |
| Scan residual faceting | A report of remaining smooth chains of planar faces | Not proof that every curved surface was recovered |

The output is a `Part::Feature`, not a parametric PartDesign model with recovered
sketches, constraints and feature history. It can be used in subsequent BREP
operations and geometry inspection, but design intent is not reconstructed.

## Quick start

Keep FreeCAD open with the updated addon and RPC running. Check
`get_assembly_capabilities().operations` includes `convert_mesh_to_brep`.
Use an open document with Undo enabled and no open transaction.

From an existing mesh:

```text
convert_mesh_to_brep(doc_name="Example", mesh_object="BaseMesh",
                     name="BaseBREP")
```

Or directly from an STL on the **FreeCAD host**:

```text
convert_mesh_to_brep(doc_name="Example",
                     source_file="/path/to/part.stl",
                     name="RecoveredPart", repair=true)
```

Supply exactly one input. Coordinates are interpreted as millimetres. Repair is
opt-in; it does not fill missing surfaces. Existing objects are not overwritten.
The source mesh's geometry, global placement and visibility are preserved.
The result is created at the source's global placement; file input uses the
coordinates stored in the STL.

The tool does **not** save the document. Review the report and inspect the
result, then save explicitly. All parameters are in the
[tool reference](tools.md#mesh-to-brep-conversion).

## Automatic workflow

1. Copy and validate the mesh; optionally repair bounded micro-defects.
2. Sew triangles into closed BREP shells and make solids.
3. Rebuild nearly coplanar patches on common planes, retaining their boundaries.
4. Recover full cylinders, layered arc profiles and local cylindrical bands.
5. Continue supported inner-wall fragments and rescan for residual faceting.
6. Check volume and bounds budgets, then create the output transactionally.

Surface recognition runs for up to four passes. It stops when no qualifying
facet chains remain or the face count no longer improves. Set
`recognize_surfaces=false` for ordinary faceted conversion without recognition.

## Results on six real parts

The following recorded benchmark uses the six STL files from micromet's
**Pan and Tilt for Standard Servo**, Thingiverse **1941574** (source geometry:
**CC BY-SA**). Every part used the same generic tool with `repair=true` and all
other tolerances at their defaults. No face, axis or dimension was selected
manually, and OpenSCAD was not an input to conversion.

| Source STL | Output faces | Planes | Cylindrical faces | Absolute volume change |
| --- | ---: | ---: | ---: | ---: |
| `PanTiltBase_std.stl` | 27 | 12 | 15 | 0.0397371% |
| `PanMount7.stl` | 45 | 34 | 11 | 0.0306401% |
| `sideplateservo_std.stl` | 24 | 15 | 9 | 0.0226912% |
| `sideplateshaft_std.stl` | 14 | 9 | 5 | 0.0109199% |
| `tiltservomount_std.stl` | 31 | 21 | 10 | 0.0279310% |
| `IRTmount_pantilt.stl` | 24 | 14 | 10 | 0.0253366% |

Each result contains one closed, valid solid, passes extended OCCT checks, and
has no residual chains detected by the scanner below. Cylindrical-face counts
are surface-patch counts, not necessarily the number of distinct holes.

For example, the tilt platform starts with **2,884 STL triangles** and ends with
**31 faces: 21 planes and 10 cylindrical surfaces**. Recovery includes the large
opening, mounting holes, shaft bore, outer support arc and both sloped rib
planes. Oblique bore exits have analytic ellipse edges rather than segmented
outlines. These are measured surface/curve types, not just a smooth screenshot.

The six-part result was saved, closed and reopened. Volumes, face counts/types,
placements and residual faceting were checked **before** recomputing, solving
joints or changing Shape/Placement. Independent checks also used reviewed
expected cylinder locations for the tilt platform and material/empty-space
probes to catch an unwanted wall across the PanMount7 cutout.

These benchmark models, JSON reports and independent verification macros live
in the separate `androsaur-stand` project, not in this repository:

- `cad/parts/automatic-recovery-check/{README.md,report.json,automatic-recovery-check.FCStd}`
- `cad/parts/tilt-platform-automatic-recovery/{README.md,report.json,tilt-platform-automatic-recovery.FCStd}`
- `scripts/verify-automatic-recovery-suite.FCMacro`
- `scripts/verify-automatic-tilt-recovery.FCMacro`

This is a finite benchmark, not a guarantee for arbitrary STL files.

## Reading the result

The tool returns a JSON report and stores conversion measurements in the output
object's `ConversionReport` property.

| Report field | What to review |
| --- | --- |
| `valid`, `closed`, `solid_count` | Basic solid validity and component count |
| `source_facets`, `face_count`, `surface_types` | Input complexity and actual output surface types |
| `volume_change_percent`, `max_bounds_change_mm` | Changes against the configured budgets |
| `repairs` | Repairs attempted, measured vertex movement and validation |
| `surface_recognition.planar_patches` | Shared-plane recovery and vertex residuals |
| `surface_recognition.restored` / `skipped` | Full-cylinder recovery and skip reasons |
| `surface_recognition.extruded_profiles` | Layered-profile arc recovery |
| `surface_recognition.local_cylinders` | Partial-cylinder recovery and skip reasons |
| `surface_recognition.analytic_seed_completion` | Connected inner-wall completion |
| `surface_recognition.automatic_passes` | Face-count progress and remaining chains per pass |
| `surface_recognition.faceting_check.remaining_regions` | Unresolved qualifying facet chains |
| `reconstruction_status` | Recognition outcome, separate from solid validity |
| `saved` | Always `false` at conversion time; saving is a separate action |

`reconstruction_status` is `not_assessed` when recognition is disabled,
`residual_faceting` when chains remain, or
`no_smooth_facet_chains_detected` when none were detected. The last value does
not mean that every arbitrary curved surface has been recognized.

An unsupported recognition step can leave its input shape unchanged and report
a reason. Therefore a successful conversion or an empty full-cylinder
`skipped` list alone does not establish complete analytic recovery.

## Input validation and bounded repair

Open, non-manifold, still self-intersecting, zero-volume and invalid meshes are
rejected. Separate non-overlapping closed shells become solids in a compound;
nested or overlapping shells are rejected rather than silently filling cavities.
If the original signed mesh volume cancels to zero, conversion is rejected even
with `repair=true` because volume comparison has no usable baseline.

Bounded snapping is opt-in (`repair=true`) and runs only when self-intersections
remain. It tries at most three decimal grids, finest first. The measured movement
of every vertex must be at most `min(tolerance_mm, fit_tolerance_mm, 0.001)` mm;
intersection segments longer than twice that limit are rejected upfront.
Zero-tolerance `fixDegenerations(0)` can retriangulate degenerate facets, but
only planar changed patches with identical oriented boundaries are accepted.
The candidate must have no self-intersections, be closed/manifold, retain point
and facet counts, and stay within the volume-change budget. Missing surfaces are
not filled. Extended OCCT BREP checks must pass before and after recognition.
The `bounded_decimal_grid_snap` entry in `repairs`
records the grid, actual displacement, changed vertices, intersection counts,
retriangulated triangles, planar patches and extended BREP validation.
This is approximate coordinate repair, not recovery of original design dimensions.

## Plane recovery

The report includes source and output volumes, volume change, bounding-box
change, facet/face/solid counts, surface types and attempted repair operations.
Volume and bounds checks are **not** a maximum surface-deviation guarantee.
Before cylinder recognition, `surface_recognition.planar_patches` reports
near-plane reconstruction. Largest-area seed faces define fixed support planes;
all candidate vertices must fit within `fit_tolerance_mm`, and normals must
agree (`dot >= 1-1e-7`). No growing-plane drift or rectangle guessing is used.
Faces are rebuilt on shared planes with their original trim wires, including
holes, and then unified. Only copied topology is edited. BREP edge/vertex
tolerances are adjusted within the fit budget (with OCCT's 1e-7 mm floor).
Closed shells, valid solids and extended OCCT checks are mandatory; failures
return the original shape with a reason. Reports give plane origins/normals
in source-local coordinates, vertex residuals and before/after face counts.
Disconnected coplanar regions are not filled or bridged.
## Full cylinders

With recognition enabled, `surface_recognition` reports `restored` and `skipped`
cylinders, their axes, radii, axial spans, fitting errors and estimated chord
deviations. Recognition uses mesh geometry, not filenames, OpenSCAD sources or
part-specific dimensions. It requires a complete circular polygonal contour
with at least 12 segments, no angular step over 30 degrees, and matching side
facets. Duplicate/collinear subdivision points inserted along straight polygon
edges by boolean intersections are discarded before fitting; they are not
required to lie on the fitted circle. Reports include `source_contour_vertices`
and `circle_sample_vertices`. A subdivided coarse polygon is still rejected
if fewer than 12 actual corners remain. Cones, spheres, freeform surfaces and
unsupported intersecting contours can remain faceted; an empty `skipped` list does not prove every
curved surface was recognized. Multiple analytic patches can represent one
interrupted cylinder.

To avoid tiny residual facets at rounded STL vertices, the reconstructed radius
uses a small outward envelope (maximum side-vertex radius plus 0.1 times the fit
tolerance). Both the least-squares fitted radius and the resulting radius are
reported. The change counts against the surface tolerance. Outer bands receive
an annular replacement only where material support is verified; a crossing
opening causes that candidate to be skipped. Lower partial transition regions
can retain facets. Sources are never edited.

## Local cylinders and automatic passes

Recognition runs automatically for up to four passes, stopping when no
smooth facet chains remain or face count no longer improves. `local_cylinders`
handles partial arc bands even when other features cross the extrusion axis.
It confirms side facets, a common axial band and material support before an
outer sleeve replacement. End flaps are trimmed by adjacent actual planes;
those planes never clip the whole central arc (a plane can intersect a circle twice).
`analytic_seed_completion` can finish connected inner-wall fragments using
an existing cylinder, without bridging separate components through a blind floor.
Full outer-cylinder replacement is restricted to the proven common axial band;
partial end layers are handled locally, not filled with a full-circle skin.

Circle fitting retries chord simplification within the fit budget for rounded
boolean subdivision points. Polygonal side vertices on chords may lie inward
of the radius by the measured sagitta. Oblique open exits receive a bounded
axial cutter extension; an axial blind floor never does.

`automatic_passes` records progress. `faceting_check.remaining_regions` lists
unresolved connected chains of at least four planar faces with adjacent normal
changes between 0.05 and 30 degrees. `reconstruction_status` distinguishes
residual faceting from solid validity. An empty scan is **not** a mathematical
proof of completeness for arbitrary freeform geometry or very short chains.
`local_cylinders.angular_spans_deg` lists parameter spans of resulting cylindrical
faces intersecting the repaired band. A merged face may also extend into adjacent
bands. The fitted seed arc may be shorter than the final plane-trimmed arc.

## Layered profiles and partial arcs

For **partial arcs**, `surface_recognition.extruded_profiles` reports a second
stage. It accepts solids made of parallel extruded layers, with all planes
parallel/perpendicular to a common axis and cylinders parallel to that axis.
Oblique or crossing surfaces cause this stage to be skipped, preserving the
full-cylinder result. There may be at most 32 axial levels.
Small side-triangle normal errors are judged by their implied lateral drift
over the axial span, limited by `fit_tolerance_mm`, rather than a fixed angular
threshold. `max_source_side_drift_mm` records the accepted source drift.

Each layer's section is rebuilt with straight edges unchanged, existing analytic
edges preserved, and eligible chord chains replaced by exact circular arcs.
Arcs must have at least five samples, a sweep of at least 45 degrees, consistent
turning direction, steps no larger than 30 degrees and fitting/chord deviation
within the supplied tolerances. The arc's two endpoints are retained; tangent
continuity to neighboring lines is not inferred or guaranteed. Closed circles
are not split into arcs. Nested section wires preserve pockets and holes;
different layers preserve blind pocket floors. The layers are extruded, fused
and validated before replacing the result. No document mutation occurs until
all native conversion stages finish and the transaction begins.

The profile report includes `applied`, `layers`, recovered `arcs` and a `reason`
when a stage fails. Arc entries contain world-space `axis_point`, `axis`, axial
range, radius, sweep and fitting/deviation estimates; `profile_center_mm` is in
the layer's local 2D basis. Global volume and bounds checks still apply.

This is geometric approximation, not recovery of original design dimensions.
Global volume/bounds limits still apply and reject excessive changes. In
particular, coarse STL cylinders may need a larger explicit volume tolerance.

## Jobs, cancellation and saving

The conversion uses the native job API. If it returns `job_id`, poll
`get_assembly_job` instead of submitting the operation again. For explicit
idempotency:

```text
start_assembly_operation(operation="convert_mesh_to_brep",
                         arguments={"doc_name": "Example",
                                    "source_file": "/path/to/part.stl",
                                    "name": "RecoveredPart", "repair": true},
                         request_id="recover-part-001")
get_assembly_job(job_id="...")
```

Cancellation before commit rolls back output creation. Native OCCT calls can
only be cancelled at safe checkpoints. No output object is created until the
conversion stages and final budgets pass. The tool does not save files or
change the active view. See [job lifecycle](tools.md#assembly-job-lifecycle).

## Limits: reconstruction is not redesign

- Cones, spheres, splines and arbitrary freeform surfaces are not reconstructed
  by the current analytic recognition stages and may remain faceted.
- A coarse intentional polygon must not become a cylinder merely because its
  edges contain many subdivision vertices.
- Open or seriously damaged meshes need separate repair; missing walls and
  cavities are not guessed or filled.
- Volume and bounding-box agreement are not a maximum surface-distance proof.
- Fitted dimensions are approximate, not recovered nominal design dimensions.
- Arc endpoints are preserved, but tangency to adjacent straight walls is not
  inferred. A visible shoulder can be an original non-tangent intersection,
  not a reconstruction defect. Adding a tangent fillet would be redesign.
- Solid validity, recognition completeness and correct mechanical assembly are
  different checks. Converting a part does not establish its mounting side,
  joint correspondence, spacing or collision-free assembly.

Do not silently raise tolerances just to obtain success. Review the reported
deviation, unsupported regions and the intended use of the part.

## Verification and regression checks

Unit and mocked-runtime tests cover fitting, rejection of misleading contours,
plane grouping, bounded repair, forwarding and transactional cancellation:

```bash
uv run pytest -q
```

The live smoke script connects through an actual stdio MCP server to FreeCAD.
It creates disposable test documents; use a dedicated session and keep other
unsaved work protected. It checks conversion, source/placement preservation,
failure cases, analytic geometry, oblique exits, blind bores/slots and
save/reopen behavior. Run live checks serially.

```bash
uv run python scripts/smoke_mesh_conversion.py --stl /path/to/base.stl --require-cylinders
uv run python scripts/smoke_mesh_conversion.py --stl /path/to/sideplate.stl --require-cylinders --require-profile-arcs
uv run python scripts/smoke_mesh_conversion.py --stl /path/to/tiltservomount_std.stl --repair --require-roundoff-repair --require-cylinders --require-planar-patches --require-local-cylinders --require-no-faceted-regions
```

The last command's requirements describe that particular regression input,
not all valid meshes. A mesh without defects need not perform roundoff repair.

For saved-model regressions, inspect the reopened result before recompute,
solver execution or geometry/placement writes. Expected geometry should be
specified independently of the candidates chosen by the converter: otherwise
a check can confirm its own assumptions or repair the defect it should detect.

## Implementation map

| Module | Responsibility |
| --- | --- |
| [runtime.py](../addon/FreeCADMCP/freecad_mcp_core/runtime.py) | Input validation, bounded repair, sewing, global budgets and transaction |
| [mesh_planes.py](../addon/FreeCADMCP/freecad_mcp_core/mesh_planes.py) | Common support planes with retained trim wires |
| [mesh_surfaces.py](../addon/FreeCADMCP/freecad_mcp_core/mesh_surfaces.py) | Full cylinders, connected wall completion, repeated passes and residual scan |
| [mesh_profiles.py](../addon/FreeCADMCP/freecad_mcp_core/mesh_profiles.py) | Arc recovery in parallel extrusion layers |
| [mesh_local_cylinders.py](../addon/FreeCADMCP/freecad_mcp_core/mesh_local_cylinders.py) | Local partial cylinders trimmed by neighboring planes |
| [server.py](../src/freecad_mcp/server.py) | MCP tool entry point |
