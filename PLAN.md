# PLAN — round 7 (2026-11-26)

Status: **approved 2026-11-26**, in progress. Completed plans are archived in `docs/plans/`.

Release at the end of the round: **0.3.0** (spec 12).

Scope (decided 2026-11-26): the **Graph block** is built now; the **FE view block** gets
its spec reviewed and its risky part prototyped now, and is built in round 8 (0.4.0).
The former round 7 content (data processing tool, multi-select, user block library) moves
back by one round (see *Moved out*).

Each step starts with its spec change (first proposal, user reviews), then code and tests.

## A. Graph block (spec 13)

- [x] 1. **Spec 13 reviewed** 2026-11-26: house style only (no company style file now);
      default size 16 × 9 cm at **300 dpi**.
- [x] 2. **House style** `apb.figures.style` (fonts, Okabe–Ito colours, figure size in cm),
      used by the Graph block and offered to Python scripts (`house_style()`); the Python
      *Plot* template uses it. Figures carry their width (`apb_width_cm`), which the
      report figure section uses as its default width.
- [x] 3. **Block `figure.graph`**, category *Figures*: line, scatter, bar (grouped /
      stacked), horizontal bar, step, area, histogram, box; X, Y columns, *Series by*;
      axis labels from the units; mass in kg. Checks before the run (columns exist,
      numeric Y, positive values on log axes). Tests per plot type (image produced, axes,
      labels, legend entries), no image comparison.
- [x] 4. **Axes and annotations:** log axes, limits, grid, legend position, reference lines
      (value typed or from a connected scalar), bar labels with a number format,
      highlighting of bars below a limit.
- [ ] 5. **Panel:** kind as icon buttons, only the fields of the chosen kind, column
      drop-downs, reference line rows; live preview: a change re-runs only this block
      from its stored input (debounced). Browser tests.
- [ ] 6. **Example:** the test case pipeline gets two graphs in the report: effective mass
      fraction per mode (bars) and CBUSH margins per fastener and load case with `MS = 0`.
      README: the Graph block in the block list.

## B. FE view block — spec and prototype (spec 14, built in round 8)

- [x] 7. **Spec 14 reviewed** 2026-11-26: PyVista a normal dependency; orthographic and
      perspective both available (orthographic default); basic coordinate system only to
      start; elements CQUAD4, CBUSH, RBE2, CONM2 **plus boundary conditions** (SPC/SPC1,
      GRID `PS`) as symbols.
- [ ] 8. **Architecture rules for separable packages** (spec 00, short new section): a
      tool package (`apb.femview`, later the data processing tool) imports nothing from
      `apb.engine`/`apb.gui`, exchanges data through plain types/files, and plugs into the
      pipeline through a block package. Only the rules needed by spec 14 now; the
      per-tool proposal stays with the data processing tool.
- [ ] 9. **Prototype, headless rendering** (throwaway, `tools/prototypes/femview_render.py`,
      not shipped): read `modal.h5` (grids, CQUAD4, CBUSH, RBE2, eigenvector of mode 1),
      plus the SPC grids, render a PNG off-screen with PyVista from a fixed camera, on Linux and Windows in CI
      (separate CI job, on the test branch only). Result in spec 14 §3.4: what CI needs
      (xvfb / OSMesa), render time, install size. Plus a quick check that a glTF export
      of the same mesh loads in `ui.scene` and the camera can be read back.

## C. Release

- [ ] 10. Release 0.3.0: CI on a test branch, Windows test by the user (Graph block: each
      plot type, reference line, live preview, report), changelog, version, tag, push to
      `main`, GitHub release, test branch deleted.

## Moved out

- **FE view block, build** → round 8 (0.4.0), from spec 14 and the prototype (step 9).
- **Data processing tool** → round 9, spec first, after the architecture rules (step 8).
- **Canvas multi-select** → round 9. Agreed: Ctrl+click and box select with Shift+drag
  (plain drag keeps panning), move/delete together, panel shows "N blocks selected".
- **User block library** → round 9, spec first (*Save as library block…* on a Python block,
  user block folder per computer or shared team folder; related: macro blocks).
- **Round 5 part B** (load case names from the file, several subcases in one run, CBUSH
  margin example pipeline; spec 02 §3.10) → backlog.
- **Remote branch `test/round-6`** still to be deleted (blocked for Claude by the
  permission check; `git push origin --delete test/round-6`).
