# PLAN — round 6 (2026-11-23)

Status: **decisions taken 2026-11-23, waiting for go**. Completed plans are archived in `docs/plans/`.

Release at the end of the round: **0.2.0** (spec 12).

Scope: the findings from the Windows 11 test (`my_notes.md`, "Testing of current version").
The **data processing tool** moves to round 7 (release 0.3.0): the findings below are a full
round on their own. Multi-select and the architecture section also move to round 7.

Each step starts with its spec change (first proposal, user reviews), then code and tests.

## A. Start-up, window and shutdown (spec 06 §3.6, README)

- [x] 1. **README, Windows without uv:** use `python -m venv .venv` (the `py` launcher only
      comes with the python.org installer). Mention `py` as an alternative.
- [x] 2. **Closing the window must stop the server** (Windows/Edge). Today the server only
      stops when the watched Edge process ends; on Windows Edge can hand the window to
      another process (or keep running in the background), so the end is never seen and the
      command line hangs. Fix: in app mode the idle monitor also applies (no page connected
      for N s → stop), whether or not the process is watched. Test with a fake process that
      never ends.
- [x] 3. **Say what was opened:** on start, print one line to the console, e.g.
      `Opened an Edge app window (C:\...\msedge.exe, own profile) at http://127.0.0.1:8765`,
      and document in the README why it is a separate window (own profile, so closing it
      can stop the tool) and how to get a tab in the normal browser (`--window browser`).

## B. File dialog (spec 06)

- [ ] 4. **Several suffixes per file field:** the Nastran input field lists `.bdf .dat .nas
      .blk` (today `.h5`, because the suffix is guessed from the word "nastran" in the
      label). The suffixes come from the block's parameter definition, not the label. Add a
      "show all files" switch.
- [ ] 5. **Long paths readable:** the folder path is shown cut at the start (`…\project\fem`)
      so the end is always visible, full path as tooltip, and it can be edited/pasted.

## C. MSC Nastran analysis block (spec 09)

- [ ] 6. **Nastran settings in user settings, override per block:** a *File > Settings*
      dialog that edits the `[nastran]` section of `config.toml` (path, command arguments,
      default run mode). The block shows the inherited value as placeholder ("from
      settings: …") and only stores a value when the user overrides it.
- [ ] 7. **Clearer status and warnings:**
      - The warning triangle comes from a `WARNING` line in the block's log (here: a Nastran
        warning message in `local_loads.f06`), not from the missing Nastran path. The panel
        will list the warning lines at the top, so the triangle can be explained.
      - "'nastran' not found" is shown red only when a run is possible; with run mode
        *never* it is grey info ("not needed: run mode is never").
      - "made by this tool, input unchanged" gets a label ("Results:") and a tooltip.
- [ ] 8. **`.h5` not always required:** the run is only an error without `.h5` (and without
      HDF5 request in the deck) if the `h5` output is connected. With only the `f06` output
      used (e.g. modal run read from the f06), a missing `.h5` is fine.

## D. Results from the `.f06` (spec 02, new section)

- [ ] 9. **f06 table reader** (shared parser in `blocks/nastran/f06.py`, tested on
      `examples/fem/modal.f06` and `global_loads.f06`):
      - Block *Nastran f06: modal effective mass fraction*: the translation and rotation
        tables combined into one table `MODE, FREQUENCY, T1, T2, T3, R1, R2, R3` (plus the
        sum rows as a separate option).
      - Block *Nastran f06: grid point weight generator*: mass and C.G. per direction,
        same columns as the HDF5 masses block (`DIRECTION, MASS, X_CG, Y_CG, Z_CG`), so the
        two are interchangeable downstream.

## E. Table blocks (spec 03)

- [ ] 10. **Write CSV** block (`table.write_csv`): one file per connected table, units as a
      second header row or in the header (same option as Write Excel), separator and
      decimal sign selectable (Excel in Swedish locale expects `;` and `,`).
- [ ] 11. **Renaming columns:** already possible with the *Select* block (`rename`), but
      hard to find. Rename it *Select / rename columns* and give the panel a rename table.
      (The data processing tool in round 7 will also cover it.)
- [ ] 12. **Sheet / table names:** document that the default name is the table's title,
      carried in `DataFrame.attrs["title"]` and set by the source block (e.g. the HDF5
      result name, the CSV file name). Show the title in the table preview so it is
      visible, and let the Python block set it (`df.attrs["title"] = ...`, in the template).

## F. Python block (spec 04)

- [ ] 13. **Ports and code in step** (approach agreed 2026-11-23):
      - Ports stay the definition of the block. Adding, renaming or removing an input port
        rewrites the `def run(...)` line of the script (only that line).
      - When the script is saved, arguments of `run` that are not ports are offered as new
        input ports ("Add port `loads`?"); ports missing from the signature are flagged.
      - Output ports: returned keys that are not ports, and ports never returned, are
        reported after the run (they cannot be read reliably from the code beforehand).
- [ ] 14. **Debugging:** `print()` already goes to the block log; make it usable:
      - On an error, the log opens and shows the traceback cut to the user's script, plus
        the local variables of the failing line (tables as shape, columns and first rows).
      - A helper `show(obj, name)` (importable as `from apb import show`) that adds a table
        or value as an extra tab in the block's preview, also when the run fails later.

## G. Release

- [ ] 15. Release 0.2.0: changelog, version, tag, push. CI on a test branch first (steps 2,
      4 and 5 touch Windows-specific behaviour), then a short re-test on Windows by the user.

## Moved out

- **Data processing tool** → round 7 (0.3.0). Spec first, built on the architecture section.
- **Architecture: related tools and their boundaries** (spec only, `00-architecture.md`;
  rules for separable tools, per-tool proposal for review, HDF5 viewer check) → round 7,
  before the data processing tool.
- **Canvas multi-select** → round 7. Agreed: Ctrl+click and box select with Shift+drag
  (plain drag keeps panning), move/delete together, panel shows "N blocks selected".
- **Round 5 part B** (load case names from the file, several subcases in one run, CBUSH
  margin example pipeline; spec 02 §3.10) → backlog (decided 2026-11-23).
