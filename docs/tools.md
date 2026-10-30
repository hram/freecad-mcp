# Tools

[Back to README](../README.md) · [Configuration](configuration.md) · [Code execution](execution.md)

## Available tools

The server exposes **32 tools**: 17 original tools, 14 additions for verified
assembly/document save status, and mesh-to-BREP conversion. The tables below
list all of them.

### Documents and objects

| Tool | Purpose |
| --- | --- |
| `create_document` | Create a new FreeCAD document. |
| `list_documents` | List open document names; `include_status=true` returns a save-state snapshot instead. |
| `get_document_status` | Read save state, file paths and recompute state for one or all open documents. |
| `reload_document` | Close and reopen a saved document to pick up external file changes, such as results from a headless script. |
| `create_object` | Create an object in a document. |
| `edit_object` | Edit an object's properties. |
| `delete_object` | Delete an object from a document. |
| `get_objects` | Get all objects in a document. |
| `get_object` | Get one object in a document. |
| `get_view` | Get a screenshot of the active view. |
| `convert_mesh_to_brep` | Convert an STL or mesh into validated BREP solids and recognize full cylindrical bands, preserving the source. |

### Execution, diagnostics and libraries

| Tool | Purpose |
| --- | --- |
| `execute_code` | Execute Python code on FreeCAD's GUI thread. |
| `execute_code_async` | Start a background computation and return its job ID; use `commit()` for document and view access. |
| `get_async_status` | Get background job state and failure tracebacks without using the GUI thread. |
| `execute_code_headless` | Run a script in a separate `freecadcmd` process and return its exit status and output. |
| `get_rpc_status` | Report RPC and GUI-dispatch health, addon version, and version check without using the GUI thread. |
| `insert_part_from_library` | Insert a part from the [FreeCAD parts library](https://github.com/FreeCAD/FreeCAD-library). |
| `get_parts_list` | List parts in the [FreeCAD parts library](https://github.com/FreeCAD/FreeCAD-library). |
| `run_fem_analysis` | Run CalculiX on an existing analysis and return summary results. |

See [code execution](execution.md) for execution modes, shared script state,
background job tracking, and timeout handling.

### Verified assembly

These tools use the fork's native addon API. Start with
`get_assembly_capabilities` to check protocol compatibility and supported
operations. See the [README workflow](../README.md#рабочий-цикл) for an example.

| Tool | Purpose |
| --- | --- |
| `get_assembly_capabilities` | Report assembly protocol, operations, job support and addon version. |
| `inspect_part` | Inspect valid world-space geometry, planar faces and cylindrical hole candidates. |
| `define_interface` | Bind a contact plane and explicitly ordered hole faces to a geometry fingerprint. |
| `plan_alignment` | Calculate rigid placement, mating gap and explicit collision scope without moving parts. |
| `preview_alignment` | Show the proposed placement as a cyan ghost with mounting axes in three views. |
| `apply_alignment` | Apply a fresh plan transactionally, recompute and verify; roll back if verification fails. |
| `check_assembly` | Independently measure current hole errors, axis angles, mating gap and scoped intersections. |
| `inspect_region` | Capture selected parts from multiple views, restoring camera and visibility afterwards. |
| `create_assembly` | Move root parts into a new native Assembly while preserving their world placements. |
| `create_fixed_joint` | Preserve an applied, verified mounting pose with native Fixed and Grounded joints. |

### Assembly jobs

| Tool | Purpose |
| --- | --- |
| `start_assembly_operation` | Submit a supported native operation with a caller-supplied idempotent `request_id`. |
| `get_assembly_job` | Poll an assembly job without waiting for the GUI; optionally include its result and PNG images. |
| `cancel_assembly_job` | Cancel a queued job or request cancellation at a safe checkpoint for a running job. |

## Mesh-to-BREP conversion

`convert_mesh_to_brep` creates a separate root `Part::Feature` from one STL
file or `Mesh::Feature`, with optional bounded repair and automatic recovery of
planes, full/local cylinders and eligible arc profiles. Source geometry,
placement and visibility are preserved. The tool does not save files.

See [Automatic STL-to-BREP reconstruction](mesh-to-brep.md) for the workflow,
six-part benchmark, recognition algorithms, verification and limitations.

```text
convert_mesh_to_brep(doc_name="Example", mesh_object="BaseMesh",
                     name="BaseBREP", tolerance_mm=0.01)
```

| Parameter | Default | Purpose |
| --- | --- | --- |
| `doc_name` | Required | Destination document, with Undo enabled and no open transaction. |
| `mesh_object` | `""` | Source mesh object's internal name. Its global placement is baked into the root output; source geometry, placement and visibility are unchanged. |
| `source_file` | `""` | Alternative STL file input. STL coordinates are interpreted as millimetres. |
| `name` | `"MeshBREP"` | New ASCII object identifier; existing objects are never overwritten. |
| `tolerance_mm` | `0.01` | Positive sewing tolerance; bounding-box limit when recognition is disabled. |
| `repair` | `false` | On a copy, remove duplicate points/facets, harmonize normals, and attempt bounded decimal-grid snapping for micro-intersections. Does not fill holes or invent missing surfaces. |
| `refine` | `true` | Merge coplanar faces after sewing. |
| `max_volume_change_percent` | `0.1` | Maximum absolute volume difference from the original mesh, as a percentage. |
| `recognize_surfaces` | `true` | Recover planes, full/local cylinder bands and eligible layered arc profiles. Set `false` for faceted-only conversion. |
| `fit_tolerance_mm` | `0.001` | Maximum distance of planar-patch vertices from the fixed support plane; radial fitting error for circle samples and matching side facets. |
| `surface_tolerance_mm` | `0.05` | Maximum estimated polygon-to-cylinder change (chord sagitta, fitting error and outward envelope). Bounding-box limit is the larger of this and sewing tolerance during recognition. |

### Result

Returns `document`, `object`, source provenance, validity/component counts,
facet/face counts, surface types, volume/bounds measurements, `repairs`,
`surface_recognition`, `reconstruction_status` and `saved=false`.
Conversion measurements are also stored in the object's `ConversionReport`
property. See [report fields](mesh-to-brep.md#reading-the-result).

A valid solid is not a guarantee of complete surface recovery. Unsupported
patches may remain faceted with reasons in the report. Open, non-manifold,
still self-intersecting, zero-volume and nested/overlapping-shell inputs are
rejected; separate closed non-overlapping solids are supported.
Volume/bounds limits do not prove a global surface-distance bound.

### Execution

Requires an updated addon advertising the operation through
`get_assembly_capabilities`, an open document with Undo enabled and no open
transaction. If a `job_id` is returned, poll `get_assembly_job`. Explicit
submission through `start_assembly_operation` supports idempotent request IDs.
Creation is transactional and cancellation before commit rolls it back.
See [jobs and saving](mesh-to-brep.md#jobs-cancellation-and-saving).

## Document save status

`get_document_status(doc_name="")` inspects all open documents; pass an internal
document name to inspect one. `list_documents(include_status=true)` returns the
same snapshot for all documents. These calls do not save or recompute anything.

| Field | Meaning |
| --- | --- |
| `modified` | Actual `Gui.Document.Modified` flag, or `null` if unavailable. |
| `saved_once` | Whether the document has been saved before (`App.Document.isSaved()`). |
| `file_path`, `file_exists` | Associated file path and whether it exists on disk. |
| `needs_save` | `true` for a new document, missing file or GUI modification; otherwise the GUI flag, possibly `null`. |
| `needs_recompute` | Whether the document needs recomputing, independently of save state. |

The response includes a `documents` list and a `summary` with `open_count`,
`needs_save_count` and `unknown_count`. Check it before closing or reloading a
document. `saved_once=true` does not mean all edits are saved, and `null` does
not mean clean. `App.save()` / `saveAs()` can leave the GUI modified flag set;
the tool reports that flag rather than comparing file contents.

## Verified assembly parameters and workflow

Use this sequence after checking capabilities:

```text
inspect_part → define_interface → plan_alignment → preview_alignment
             → apply_alignment → check_assembly → inspect_region
```

`doc_name` and `part` refer to internal document and object names. Geometry uses
world coordinates in millimetres. Both mounting interfaces must belong to the
same document.

| Tool | Parameters and defaults |
| --- | --- |
| `inspect_part` | `doc_name`, `part`; `feature_limit=100` (1–500 per face type), `plane_offset=0`, `cylinder_offset=0`. Follow `next_plane_offset` / `next_cylinder_offset` when returned. |
| `define_interface` | `doc_name`, `part`, `plane_face`, `hole_faces`; optional `owner`, defaulting to `part`. Select at least two distinct, ordered, full inner cylindrical faces perpendicular to and opening onto the contact plane. |
| `plan_alignment` | `fixed_interface`, `moving_interface`; `gap_mm=0`, `tolerance_mm=0.01`, `angular_tolerance_deg=0.05`, `collision_tolerance_mm3=0.001`, optional `obstacles` list. |
| `preview_alignment` | `plan_id`; `size=1024` square pixels (256–1536). Requires a fresh, unapplied plan. |
| `apply_alignment`, `check_assembly` | `plan_id`. Apply accepts only a fresh, unused plan; check measures the current position without moving it. |
| `inspect_region` | `doc_name`, `parts` (1–20); optional `views` (1–6), default `Isometric`, `Top`, `Right`; `size=1024` (256–1536). The document's 3D view must be active. |
| `create_assembly` | `doc_name`, `parts`; `name="Assembly"`. Parts must be root objects. |
| `create_fixed_joint` | `plan_id`, `assembly_name`; `joint_name="MountingJoint"`. |

Hole order defines correspondence, including symmetric patterns. A positive
gap follows the fixed contact face's outward normal; negative gaps are rejected.
Hole diameter compatibility remains a separate check.

Verification reports `passed`, `hole_errors_mm`, `max_hole_error_mm`,
`hole_axis_angles_deg`, `opposing_faces_error_deg`, `gap_mm` and overlap volumes
in `collisions`. Only the selected moving geometry is checked against the fixed
part and explicitly supplied `obstacles`. Report `collision_scope` with the
numbers: `whole_assembly_checked` is always `false`.

Verified placement supports `Part::Feature` and `PartDesign::Body`. For
PartDesign, inspect and define the interface on the Body itself so `part=owner`.
`App::Link` and movement of `App::Part` containers are unsupported. Mutations
require Undo enabled and no open transaction, and do not save files.

For a persistent joint, create the Assembly **before** defining interfaces and
planning. Both owners must be direct members of that Assembly, with no existing
joints. Apply and verify the alignment, then call `create_fixed_joint`. Its
connectors preserve relative pose and are detached from face topology. Moving
parts with `apply_alignment` inside an Assembly that already has joints is
rejected.

Geometry or placement changes invalidate interfaces; document changes can
invalidate plans. Define and plan again after moving parts into an Assembly.
Closing/reopening a document changes its identity. Interface and plan caches
retain at most 256 entries each and reset on FreeCAD restart or runtime update.
See [verification boundaries](../README.md#границы-проверки) for further limits.

## Assembly job lifecycle

Ordinary assembly tools wait up to 20 seconds for a native job, then return its
`job_id` if still running. Continue with `get_assembly_job`; do not submit the
operation again. For long operations, submit explicitly:

```text
start_assembly_operation(operation="apply_alignment",
                         arguments={"plan_id": "..."},
                         request_id="mount-bracket-001")
get_assembly_job(job_id="...", include_result=true)
```

Use an operation from `get_assembly_capabilities().operations`; `arguments`
contains that operation's parameters. `request_id` must contain 1–128 characters.
Repeating it with the same operation and arguments returns the same job; changing
the arguments is rejected. This guarantee lasts while the job remains in addon
history: up to 8 active jobs and 32 completed jobs. Jobs survive MCP-server
restart, but FreeCAD exit or addon activation resets history.

States are `queued`, `running`, `done`, `failed`, `cancelled` and
`outcome_unknown`. The last means GUI execution exceeded its dispatch budget
and may still finish: keep polling the original job. A timeout does not prove
that a mutation failed.

`cancel_assembly_job(job_id)` cancels queued work or requests cancellation at a
safe checkpoint. Running OCCT calls cannot be interrupted; transactional
mutations roll back on checkpoint cancellation. Once committing starts,
cancellation is refused. `accepted=true` confirms only the request: poll until a
terminal state. Assembly jobs are separate from `execute_code_async` jobs,
which use `get_async_status`.

## Screenshot options

The following tools return optional screenshots: `create_object`, `edit_object`,
`delete_object`, `execute_code`, `insert_part_from_library`, `get_objects`,
`get_object`, and `run_fem_analysis`.

| Parameter | Default | Purpose |
| --- | --- | --- |
| `include_screenshot` | `true` | Set to `false` for text-only feedback, such as analytical scripts or intermediate steps. |
| `view_name` | `"Isometric"` | Orient the returned screenshot, for example `"Front"`, `"Top"`, or `"Right"`. |

The [`--only-text-feedback` flag](configuration.md#text-feedback-and-screenshots)
suppresses these optional screenshots regardless of `include_screenshot`.

Use `get_view` to request a screenshot explicitly; it is available even with
`--only-text-feedback`. It takes `view_name` and optional `width`, `height`, and
`focus_object` parameters. Supported views are `Isometric`, `Front`, `Top`,
`Right`, `Back`, `Left`, `Bottom`, `Dimetric`, and `Trimetric`.

`inspect_region` and `preview_alignment` return native MCP PNG images and require
image feedback: they reject calls when `--only-text-feedback` is enabled.
`inspect_region` supports `Isometric`, `Top`, `Front`, `Right`, `Left`, `Back`
and `Bottom`. These images complement numeric verification.

## FEM analysis

`run_fem_analysis` runs the CalculiX solver on an existing `Fem::FemAnalysis`
container. It auto-creates a `SolverCcxTools` if the analysis has none and returns
max von Mises stress, max/min displacement, node count, and the solver's working
directory. The default `timeout` is 600 seconds.

See [`examples/cantilever_fem.py`](../examples/cantilever_fem.py) for an end-to-end
example, including geometry, material, mesh, constraints, and an analytical
comparison. For long analyses, configure the client to allow the
[queue and execution timeout budgets](execution.md#gui-dispatch-timeouts).
