# Changelog

All notable changes to Potato Pipeline are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is below 1.0,
a minor release may break pipeline files; such changes are listed under **Breaking**.

## [Unreleased]

## [0.3.0] - 2026-11-28

### Added
- **Graph** block (category *Figures*): line, scatter, bar (grouped or stacked), horizontal
  bar, step, area, histogram and box plots from a table. One series per Y column or per
  value of a column (e.g. load case), axis labels with units, log axes, limits, reference
  lines (typed or from a connected value), values on bars, bars below a limit in red. The
  panel shows only the fields of the chosen plot type, and the preview updates while you
  edit when everything before the block is up to date.
- One house style for all figures (`from apb.figures import house_style, new_figure`), used
  by the Graph block and the Python *Plot* template. Figures are drawn at their size in the
  report (default 16 × 9 cm, 300 dpi), so the text has its real size.
- The test case report shows the effective mass per mode and the CBUSH margins as graphs.

### Changed
- Word report figure sections: empty width and dpi now mean the figure's own size (else
  15 cm and 200 dpi as before).

## [0.2.0] - 2026-11-26

### Changed
- Python block: *Inline code* is now called *Code in the pipeline file*, with a help text
  saying what it is (code stored in the `.apb.json` instead of a `.py` file).
- *Select columns* is now *Select / rename columns*; once the input is known, renaming is a
  table with one row per kept column.
- On start, the console says how the GUI was opened (which browser, separate app window
  with its own profile) and how to get a normal browser tab instead.
- README: *Adding your own blocks* — from a Python block to a block in the library.
- README: Windows set-up uses `python -m venv` (the `py` launcher is not always installed).
- Mass is written in **kg** by *Word report* tables, *Write Excel* and *Write CSV*
  (`t → kg`, `t·mm² → kg·mm²`), switch *Mass in kg* (on by default). Inside the pipeline
  a model in mm keeps the tonne, so computations stay consistent; the preview explains `t`.
- Word report table: *Formats* is now *Number format per column*, with a help line, the
  format that applies when empty, and a live example of the column's first value.

### Added
- Python block: the `def run(...)` line follows the input ports (add, rename, remove);
  `run()` arguments without a port can be added as ports with one click; returned values
  that do not match the output ports are reported by name.
- Python block debugging: when a script fails, the log shows the failing line of your
  code and the variables there (tables with size, columns and first rows), and the
  preview opens that log. `from apb import show; show(df, "name")` adds a table or value
  as an extra preview tab, also when the script fails later.
- *Write CSV* block: separator and decimal sign selectable (e.g. `;` and `,` for Excel
  with European settings), units in the header or in a second row.
- The preview shows the table title, which is the default sheet name in *Write Excel*.
- Blocks *Nastran f06: modal effective mass fraction* (translational and rotational tables
  in one table) and *Nastran f06: grid point weight (mass, C.G.)*. They read the `.f06`,
  so no `.h5` is needed, and give the same columns as the HDF5 results.
- *File > Settings of this computer…*: set the Nastran command, program folder and options
  once per computer (and the window settings). A block's own value still wins; empty
  block fields show the value they inherit. Comments in `config.toml` are kept.
- The property panel lists the warnings of a block's last run, so the warning mark on the
  canvas can be explained.
- Python block: when the ports and the code differ, the panel offers the fix both ways:
  *Remove input port(s)* next to *Add … to run()*, and, when `run()` returns a dict
  literal, *Add output port(s)* / *Remove output port(s)* from its keys (checked without
  running the script). The ports stay the definition of the block.
- *File > Open recent*: the last 10 pipelines opened or saved on this computer.
- *Nastran eigenvalues* also reads the `.f06` (REAL EIGENVALUES table), with the same
  columns and units as from the `.h5`.
- Python block: output ports are also read from `result = {...}` … `return result`
  (keys added with `result["x"] = ...` included). When the keys cannot be read, the panel
  says that the outputs are checked after the run.

### Fixed
- Code typed into *Code in the pipeline file* (and JSON fields) in the property panel was
  not stored. Such code now also gets the port check and the fix buttons.
- Python block: a port change that rewrites `run()` in the script file now says that the
  file is already saved and the pipeline must be saved to keep the ports (before, the two
  could get out of step without notice).
- The canvas could not be panned with a laptop touchpad. Scrolling (two fingers, or the
  mouse wheel) now pans the canvas; pinch or Ctrl + wheel zooms around the pointer.
- A Nastran block given the wrong kind of file (an `.f06` to an HDF5 block or the other
  way round) failed with an unclear HDF5 error; it now says so and names the block to use.
- The Nastran analysis block failed when no `.h5` was written, also when only the `.f06`
  was used. The `.h5` (and the HDF5 request in the deck) is now only required when the
  block's HDF5 output is connected.
- Nastran analysis panel: "made by this tool, input unchanged" was shown in warning colour;
  a Nastran program that is not found is shown grey when the run mode is *never*.
- The file dialog of the Nastran analysis block listed `.h5` files instead of input decks.
  Each file field now names its own file types (deck: `.bdf .dat .nas .blk`), and the
  dialog has a *Show all files* switch.
- (Found by CI, never released) The file dialog did not open on Windows: a path with
  backslashes was put into an element property string and broke its parsing.
- Long folder paths in the file dialog hid their end; the path is now cut at the start,
  its parts can be clicked, and a pasted folder path opens that folder.
- The file field of *Write Excel* can name a new workbook in the dialog.
- Closing the app window did not stop the program on Windows (Edge kept its process
  running); the command line was left hanging. The program now also stops 5 s after the
  last window closed and ends the left-over browser process.

## [0.1.2] - 2026-11-22

### Changed
- CI runs once per push (branches only; a release tag no longer starts a second run) and can
  be started by hand (`gh workflow run CI`).

### Fixed
- Two browser tests that failed now and then on the slower Windows CI machines.

## [0.1.1] - 2026-11-22

### Fixed
- Windows: shortened paths in the property panel no longer mix `\` and `/`.
- Windows: the code passes the type check, and the test suite runs on Windows in CI (it did
  not get that far in 0.1.0).

## [0.1.0] - 2026-11-22

First public release.

### Added
- Pipeline engine: blocks, ports and connectors, run in dependency order. Variables can be
  overridden on the command line (`--set`). Pipeline files are readable JSON (`*.apb.json`,
  schema 1).
- Command line `apb`: `run`, `validate`, `blocks`, `new-script`, `outputs`, `clear-outputs`,
  `gui`, `h5view`, `version` / `--version`.
- Browser GUI: canvas with a block library, connectors, a property panel per block, run and
  stop, table preview and a run log. It opens in a Chrome or Edge app window, or in a
  browser tab. File dialogs can create new folders. Quit and closing the window end the
  program.
- MSC Nastran HDF5 input: result browser with a quick preview, subcase/ID/column filters,
  and SI m / SI mm unit systems. Dedicated blocks for eigenvalues, effective masses and RBE2
  loads. Verified against real MSC Nastran files.
- HDF5 viewer: browse a whole `.h5` file in its own tab, with raw and result views, copy the
  path, and export to CSV.
- Nastran analysis block: run MSC Nastran from the pipeline, with run modes, reuse of
  earlier runs and an f06 warning/fatal check.
- Table blocks: filter, select, sort, join, concatenate, envelope, compute, pick, set units,
  unit conversion, load case tables, load case names, fastener loads (tension/shear), and
  CSV/Excel read and write.
- Constants block: project constants in one table, each with its own output port.
- Requirement check block: compare a value against a limit and report pass/fail.
- Python block: user code in external `.py` files, with a code view, templates and a static
  check. Existing scripts are never overwritten.
- Word report block: sections and items placed by the user, an editor dialog, and tables
  and figures from the pipeline.
- Stored outputs: results are saved next to the pipeline, so a reopened pipeline shows them
  as already run. *Clear outputs* removes them.
- Examples: fastener loads, plate stress, and a test case with modal analysis and CBUSH
  margins built on real Nastran files.

### Breaking
- None (first release).

[Unreleased]: https://github.com/thuswm/potato-pipeline/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/thuswm/potato-pipeline/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/thuswm/potato-pipeline/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/thuswm/potato-pipeline/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/thuswm/potato-pipeline/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/thuswm/potato-pipeline/releases/tag/v0.1.0
