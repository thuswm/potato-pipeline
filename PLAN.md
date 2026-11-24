# PLAN — round 6 (2026-11-23)

Status: steps 1–14 done; steps 15–20 (Windows re-test, 2026-11-24) **approved 2026-11-24**
(ports stay the master, step 18). In progress. Completed plans are archived in `docs/plans/`.

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

- [x] 4. **Several suffixes per file field:** the Nastran input field lists `.bdf .dat .nas
      .blk` (today `.h5`, because the suffix is guessed from the word "nastran" in the
      label). The suffixes come from the block's parameter definition, not the label. Add a
      "show all files" switch.
- [x] 5. **Long paths readable:** the folder path is shown cut at the start (`…\project\fem`)
      so the end is always visible, full path as tooltip, and it can be edited/pasted.

## C. MSC Nastran analysis block (spec 09)

- [x] 6. **Nastran settings in user settings, override per block:** a *File > Settings*
      dialog that edits the `[nastran]` section of `config.toml` (path, command arguments)
      and `[gui]`. The block shows the inherited value as placeholder ("settings: …") and
      only stores a value when the user overrides it. Default run mode left out: it
      belongs to the pipeline (spec 06 §3.7).
- [x] 7. **Clearer status and warnings:**
      - The warning triangle comes from a `WARNING` line in the block's log (here: a Nastran
        warning message in `local_loads.f06`), not from the missing Nastran path. The panel
        will list the warning lines at the top, so the triangle can be explained.
      - "'nastran' not found" is shown red only when a run is possible; with run mode
        *never* it is grey info ("not needed: run mode is never").
      - "made by this tool, input unchanged" gets a label ("Results:") and a tooltip.
- [x] 8. **`.h5` not always required:** the run is only an error without `.h5` (and without
      HDF5 request in the deck) if the `h5` output is connected. With only the `f06` output
      used (e.g. modal run read from the f06), a missing `.h5` is fine.

## D. Results from the `.f06` (spec 02, new section)

- [x] 9. **f06 table reader** (parser `blocks/nastran/f06tables.py`, blocks
      `f06blocks.py`, tested on `examples/fem/modal.f06` and `global_loads.f06` against the
      `.h5`):
      - Block *Nastran f06: modal effective mass fraction*: the translation and rotation
        tables combined into one table `MODE, FREQUENCY, T1_FRAC … R3_FRAC` (option: the
        running sums `T1_SUM … R3_SUM`), as the HDF5 result `MODAL_MASS_FRACTION`.
      - Block *Nastran f06: grid point weight (mass, C.G.)*: mass and C.G. per direction,
        same columns as the HDF5 result `GRID_WEIGHT` (`DIRECTION, MASS, X_CG, Y_CG,
        Z_CG`), so the two are interchangeable downstream.

## E. Table blocks (spec 03)

- [x] 10. **Write CSV** block (`table.write_csv`): one table per file (one block per file,
      spec 03 §3.12), units as a
      second header row or in the header (same option as Write Excel), separator and
      decimal sign selectable (Excel in Swedish locale expects `;` and `,`).
- [x] 11. **Renaming columns:** already possible with the *Select* block (`rename`), but
      hard to find. Rename it *Select / rename columns* and give the panel a rename table.
      (The data processing tool in round 7 will also cover it.)
- [x] 12. **Sheet / table names:** document that the default name is the table's title,
      carried in `DataFrame.attrs["title"]` and set by the source block (e.g. the HDF5
      result name, the CSV file name). Show the title in the table preview so it is
      visible, and let the Python block set it (`df.attrs["title"] = ...`, in the template).

## F. Python block (spec 04)

- [x] 13. **Ports and code in step** (approach agreed 2026-11-23):
      - Ports stay the definition of the block. Adding, renaming or removing an input port
        rewrites the `def run(...)` line of the script (only that line).
      - When the script is saved, arguments of `run` that are not ports are offered as new
        input ports ("Add port `loads`?"); ports missing from the signature are flagged.
      - Output ports: returned keys that are not ports, and ports never returned, are
        reported after the run (they cannot be read reliably from the code beforehand).
- [x] 14. **Debugging:** `print()` already goes to the block log; make it usable:
      - On an error, the log opens and shows the traceback cut to the user's script, plus
        the local variables of the failing line (tables as shape, columns and first rows).
      - A helper `show(obj, name)` (importable as `from apb import show`) that adds a table
        or value as an extra tab in the block's preview, also when the run fails later.

## G. Findings from the Windows re-test of `test/round-6` (2026-11-24, proposal)

- [x] 15. **Eigenvalues from `.h5` or `.f06`, one block** (spec 02): *Nastran eigenvalues*
      reads the file by its suffix: `.h5` as today, `.f06` from the `R E A L   E I G E N V A
      L U E S` table, same columns and units either way (tested on `modal.f06` against
      `modal.h5`). The file field accepts both suffixes. All Nastran reader blocks give a
      plain message for a file of the wrong kind ("x.f06 is an .f06 file; this block reads
      .h5 — use *…*") instead of the h5py error.
- [ ] 16. **Panning with a touchpad** (spec 06): two-finger scroll pans the canvas (in both
      directions), pinch (Ctrl + wheel) zooms around the pointer, as in Figma/Miro.
      Drag on the empty canvas keeps panning. A plain mouse wheel then scrolls the view
      vertically (Shift + wheel horizontally); zoom stays on Ctrl + wheel and the +/- buttons.
- [ ] 17. **Recent files** (spec 06): *File > Open recent* with the last 10 pipelines
      (missing files greyed out, "Clear list"), also on the start screen. Stored per computer
      in `recent.json` next to `config.toml`, not in the pipeline.
- [ ] 18. **Python block: ports and code agree in both directions** (spec 04 §3.9). The
      test showed the gap: removing a port rewrote `run()` on disk at once, the pipeline was
      not saved, and after reopening the only fix offered was to put the argument *back*.
      - The **ports stay the master** of the block (decided in step 13), but every mismatch
        offers a fix in both directions: *Add port* / *Remove port* (ports follow the
        code) or *Update run()* (code follows the ports).
      - **Output ports from the code:** when `run()` returns a dict literal
        (`return {"margins": ..., "summary": ...}`), its keys are read without running the
        script and offered as *Add output port(s)* / *Remove output port(s)*. Other return
        styles keep the after-run report (PY-18).
      - Port edits that rewrite a **script file** say so: "run() in calc.py updated (saved);
        save the pipeline to keep the ports". (The file cannot wait for the pipeline save:
        the user may edit it in an external editor.)
- [ ] 19. **Inline code explained** (spec 04, panel): under *Advanced* the field is renamed
      *Code in the pipeline file* with the help text "Instead of a .py file: short code
      stored inside the .apb.json. Use a script file for anything longer (editor,
      version control)." Answer to the test note: it is the PY-08 option, the code is
      saved in the pipeline file itself.
- [ ] 20. **How to add a block to the library — documented, not built yet** (README,
      spec 04 §3.4): a short "Adding your own blocks" section: prototype in a Python
      block → copy into a `Block` subclass in a package with an `apb.blocks` entry point →
      it appears in the library. A user block library from the GUI ("Save as library
      block", see *Moved out*) gets its own spec in round 7.

## H. Release

- [ ] 21. Release 0.2.0: changelog, version, tag, push to `main`, delete `test/round-6`,
      and a **GitHub release** (`gh release create v0.2.0`, notes from the changelog). CI
      on `test/round-6` first, then a short re-test on Windows by the user.

## Moved out

- **Data processing tool** → round 7 (0.3.0). Spec first, built on the architecture section.
- **Architecture: related tools and their boundaries** (spec only, `00-architecture.md`;
  rules for separable tools, per-tool proposal for review, HDF5 viewer check) → round 7,
  before the data processing tool.
- **Canvas multi-select** → round 7. Agreed: Ctrl+click and box select with Shift+drag
  (plain drag keeps panning), move/delete together, panel shows "N blocks selected".
- **User block library** (from the 2026-11-24 test) → round 7, spec first. Proposal:
  *Save as library block…* on a Python block stores its script, ports, title and
  description in a user block folder (per computer, or a shared team folder set in
  *Settings*); the blocks appear in the library under *User*. Related: macro blocks
  (spec 01 §4) for saved sub-pipelines.
- **Round 5 part B** (load case names from the file, several subcases in one run, CBUSH
  margin example pipeline; spec 02 §3.10) → backlog (decided 2026-11-23).
