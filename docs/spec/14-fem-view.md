# 14 — FE View Block (images of the FE model)

Status: **first proposal** (2026-11-26). The spec is reviewed in round 7 and built in
round 8. Decided 2026-11-26: renderer **VTK via PyVista**; the first version shows the
**undeformed model** and **deformed shapes / mode shapes**. Contours come later.

## 1. Purpose

Reports need pictures of the FE model: the model itself, the deformed shape under a load
case, and the main mode shapes. Today these are screenshots from a pre/post-processor,
taken by hand and redone whenever the model changes. The FE view block makes them part of
the pipeline. The user sets the view once by rotating, panning and zooming. After that the
image is re-created on every run from the current results, with no GUI involved.

## 2. Requirements

| ID | Requirement | Prio |
|----|-------------|------|
| FV-01 | Block *FE view*: reads the model and results from an MSC Nastran `.h5` file (a file parameter or the `h5` output of the analysis block) and outputs one image (`Figure` port) | M |
| FV-02 | Undeformed model: shells as filled faces with edges, bars/beams/rods/bushes as lines, RBE2/RBE3 as spider lines, CONM2 as points; solids as their outer faces | M |
| FV-03 | Colour by element type, by property (PID), or one colour; legend for the colours | M |
| FV-04 | Deformed shape for a static subcase (`NODAL/DISPLACEMENT`) and mode shape for a mode (`NODAL/EIGENVECTOR`), with a scale factor (automatic: largest deformation = 10 % of the model size; or a fixed factor) and an optional undeformed outline | M |
| FV-05 | **The view is set interactively**: a 3D view in the GUI where the user rotates, pans and zooms. *Use this view* stores the camera in the block's parameters | M |
| FV-06 | Standard views (isometric, ±X, ±Y, ±Z) and *Fit*, as buttons and as parameter values | M |
| FV-07 | The image is rendered **headless** from the stored camera on every run (CLI, GUI, CI), the same on Windows and Linux | M |
| FV-08 | Image text: a title (default e.g. "Mode 3 – 25.71 Hz" or "Subcase 2 – deformed, scale 48"), a coordinate triad, and the scale factor | M |
| FV-09 | Only some properties or element types shown (e.g. hide the fixture) | S |
| FV-10 | Mode chosen by number, or as "largest effective mass in T1/T2/T3/R1/R2/R3" (from the effective mass table, spec 02/§D) | S |
| FV-11 | Image size in pixels and a figure width in cm for the report; white background | M |
| FV-12 | Displacement contour (magnitude or a component) with a colour bar | L (next) |
| FV-13 | Stress contour (e.g. von Mises on shells) with a colour bar | L |
| FV-14 | Several images from one block (e.g. the first N modes), animation, picking nodes/elements in the view | L |

## 3. Design

### 3.1 Packages (separable, spec 00 architecture rules)

```
src/apb/femview/          # no imports from apb.engine or apb.gui: can become its own package
    mesh.py               # Mesh: node ids + xyz (basic system), elements by type, PID; pure numpy
    nastran_h5.py         # read Mesh + displacement/eigenvector fields from an MSC .h5 (h5py)
    camera.py             # Camera (position, focal point, view up, parallel scale, projection), standard views
    render.py             # Mesh (+ deformation) + Camera + options -> PNG bytes (PyVista, off-screen)
    scene_export.py       # Mesh -> glTF for the interactive view in the browser
src/apb/blocks/fem/view.py   # the block (uses apb.femview only)
src/apb/gui/femview_editor.py # the view dialog
```

`apb.femview` reuses nothing from the GUI. That way the *mesh viewer* from the list of
helper tools can later be built on it as a standalone tool.

### 3.2 Reading the model (FV-01, FV-02)

From `NASTRAN/INPUT`: `NODE/GRID` (ID, CP, X, CD) and the element tables found in the
file, among them `CQUAD4`, `CTRIA3`, `CQUAD8`, `CTRIA6` (corner nodes), `CBAR`, `CBEAM`,
`CROD`, `CBUSH`, `CELAS1/2`, `RBE2` (`GM`/`RB`), `RBE3`, `CONM2`, `CHEXA`, `CPENTA`,
`CTETRA` (outer faces). Element types the reader does not know are listed in the block log
and skipped, so the block never fails on them.

Coordinate systems: grids with `CP = 0` / `CD = 0` are used as they are (all example
files). Rectangular systems (`CORD1R`, `CORD2R` in `INPUT/COORDINATE_SYSTEM`) are
transformed to the basic system. Cylindrical and spherical systems are supported for `CP`
and `CD` in the first version too, because models of round structures use them; if the
file holds a system the reader does not know, the block stops with a clear message.

Results: `RESULT/NODAL/DISPLACEMENT` (subcase via `DOMAIN_ID` → `SUBCASE`) and
`RESULT/NODAL/EIGENVECTOR` (mode, frequency from `SUMMARY/EIGENVALUE`). Translations
`T1–T3` are given in the `CD` system and are transformed to basic.

### 3.3 Block (FV-01 … FV-11)

`fem.view`, title *FE view*, category **Figures**.

| Port | Type | |
|------|------|-|
| `h5` (in, optional) | Text | The `.h5` path (e.g. from the Nastran analysis block); overrides `file` |
| `figure` (out) | Figure | The image (a PNG with `savefig`, like a stored figure, spec 08 §3.3) |

| Parameter | Values | Default |
|-----------|--------|---------|
| `file` | `.h5` file | empty |
| `show` | `model`, `deformed` (subcase), `mode` | `model` |
| `subcase` / `mode` | number (mode also `max T1` … `max R3`, FV-10) | first |
| `scale` | `auto` or a number | `auto` (10 % of the model size) |
| `undeformed_outline` | bool | true for deformed/mode |
| `color_by` | `type`, `property`, `uniform` | `property` |
| `hide` | properties (PIDs) and/or element types | none |
| `edges` | bool | true |
| `camera` | `{position, focal_point, view_up, parallel_scale, projection}` or a standard view name (`iso`, `+x`, …) | `iso` |
| `title` | text with `{mode}`, `{frequency}`, `{subcase}`, `{scale}` | per `show` (FV-08) |
| `width_px`, `height_px` | int | 1600 × 1000 |
| `width_cm` | number (size in the report) | 16 |

Camera values are stored in the model's basic coordinates. When the model changes, the
same view still works. *Fit* (zoom to the whole model) is applied when the camera is a
standard view name, and is not applied when the user set an explicit camera.

### 3.4 Rendering (FV-07)

PyVista (`pyvista.Plotter(off_screen=True)`) builds one `UnstructuredGrid` (faces and
lines). It applies the camera, renders, and returns the PNG via
`screenshot(transparent_background=False)`. The projection is orthographic by default
(true proportions, as engineers expect), perspective as an option. The legend, title,
triad and scale text are added as VTK 2D actors, so the image needs no extra composition
step.

**Risk to settle first (round 8, step 1):** off-screen OpenGL. Windows and Linux desktops
with a graphics driver work out of the box. A machine without a GPU (VM, CI) may need
software rendering (Mesa / `vtk-osmesa`). A prototype renders `modal.h5` on both CI
platforms before the block is built, and decides whether the CI needs `xvfb` or OSMesa.

### 3.5 Setting the view (FV-05, FV-06)

The block's panel has *Set view…*, which opens a dialog (like the report editor):

- **Left: interactive 3D view** with NiceGUI `ui.scene` (three.js, already part of
  NiceGUI). The mesh is sent as one glTF file written by `scene_export.py` (faces + edges,
  the colours as in the image), which keeps the browser fast for models of 100 000+
  elements. Rotate = left drag, pan = right drag / Shift + drag, zoom = wheel or pinch
  (same mouse use as the canvas, spec 06). The buttons are the standard views (iso, ±X, ±Y,
  ±Z) and *Fit*.
- **Right: the rendered image.** *Use this view* reads the camera from the browser
  (`scene.get_camera()`), stores it in `camera`, and renders the image with PyVista. What
  you see on the right is exactly what goes into the report. three.js and VTK draw a little
  differently (lighting, line widths), so the right side is the reference.
- The deformed shape / mode chosen in the panel is shown in the 3D view too, so the view
  can be set on the deformed model.

### 3.6 Performance targets

For 100 000 shell elements: reading ≤ 5 s, headless rendering ≤ 10 s, opening the view
dialog ≤ 5 s. The block caches the mesh per file (path + modification time) for the
session, so a second FE view block on the same file does not read it again.

### 3.7 Dependency

`pyvista` (with `vtk`, about 100 MB). Proposal: an **optional extra**
`pip install potato-pipeline[fem]` (and in the uv dev group, so CI tests it). Without it,
the FE view block shows "Needs the fem extra: pip install …[fem]" and everything else keeps
working.

## 4. Out of scope (first version)

Contours (FV-12, FV-13), several images per block, animation, picking, cutting planes,
results from `.f06` or `.op2`, other solvers (Abaqus), element coordinate system arrows,
loads and boundary conditions as symbols.

## 5. Open questions

1. Optional extra `[fem]` (smaller installation for users who do not need images) or a
   normal dependency (one install command, nothing to explain)?
2. Orthographic projection as default: right for your reports?
3. Are cylindrical/spherical coordinate systems needed in the first version (your real
   models), or are rectangular systems enough to start?
4. Which element types do your models use beyond the examples (CQUAD4, CBUSH, RBE2, CONM2)?
