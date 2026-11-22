# PLAN — round 5: GitHub introduction (2026-11-22)

Status: **approved 2026-11-22, in progress**. Completed plans are archived in `docs/plans/`.

Source: the "Next Step" section of the user's notes after round 4. Each step: spec → user
review → code → tests (`ruff format`, `ruff check`, `mypy src`, `pytest`) → user asks for the
commit. Tick the boxes as work is done.

## Decisions

| Topic | Decision |
|-------|----------|
| Name | Display name **Potato Pipeline**. Package `apb`, command `apb` and `*.apb.json` files are unchanged |
| Repository | Public, `github.com/thuswm/potato-pipeline` |
| License | None for now; the user decides later (MIT was suggested). Without a license file the code is visible but "all rights reserved" |
| Several load cases | Not part of the GitHub introduction: part B, after the push |

## Part A — GitHub introduction

- [x] 1. Archive round 4 as `docs/plans/round-4.md` and start this plan.
- [x] 2. **Display name.** "Potato Pipeline" in the README title and intro, the GUI page and
      window title, the CLI help text and the pyproject `description`. Add a note in
      `docs/spec/00-architecture.md` that the internal name stays `apb`.
- [x] 3. **Versioning** (spec first: `docs/spec/12-versioning.md`).
      - Semantic versioning (`MAJOR.MINOR.PATCH`). While below 1.0, a minor bump may break
        pipeline files. Each break is listed in the changelog.
      - The version is kept in one place, pyproject `version`. `apb.__version__` reads it
        through `importlib.metadata`.
      - `apb --version`. The GUI shows the version in Help > About and in the window title.
      - `CHANGELOG.md` in the "Keep a Changelog" format. The first entry, 0.1.0, summarises
        rounds 1–4.
      - Each release gets a git tag `vX.Y.Z`. The version goes from `0.1.0.dev0` to `0.1.0`.
- [x] 4. **Running without uv** (README). Windows and Linux steps using only Python 3.12+
      and pip:
      - Install Python from python.org (Windows: the `py` launcher).
      - `py -m venv .venv`, `.venv\Scripts\activate`, then `pip install .` from a clone, or
        `pip install git+https://github.com/thuswm/potato-pipeline` with no clone.
      - Then `apb gui …`, or `python -m apb …` if the venv is not active.
      - Verify: install into a clean venv with pip only (no uv), then start `apb --version`
        and `apb gui` against an example.
- [ ] 5. **Prepare the repository.**
      - Check what becomes public: `CLAUDE.md`, `PLAN.md` and `docs/plans/` stay as project
        history. `my_notes.md` is already git-ignored. The example FEM data is fake data. No
        local paths or personal data in tracked files (already checked: none found).
      - pyproject: `[project.urls]` (Homepage, Issues), `authors`.
      - README: short and fit for GitHub (what it is, screenshot, install with uv, install
        without uv, quick start, development, link to the specs).
      - No license file in this round (decision 2026-11-22). Adding one later is a separate step.
- [ ] 6. **Push.** `gh repo create thuswm/potato-pipeline --public`, push `main` and tag
      `v0.1.0`. Check that CI (Ubuntu and Windows, Python 3.12 and 3.13) passes on GitHub and
      fix any failures.

## Part B — Several load cases in one table (after the push)

Need: a Nastran analysis has several load cases (subcases), and each one outputs e.g.
CBUSH forces. The analyst checks all load cases, so they must all be in **one** table.

The HDF5 result block already reads every selected subcase into one long table with a
`SUBCASE` column (empty selection = all subcases). Because all example files have a single
subcase, this has never been shown working end to end.

- [ ] 7. **Spec update** (`02-nastran-hdf5-input.md`): the multi-load-case use case, the
      table layout (one row per element × location × subcase), and how load-case names
      (subcase `LABEL`/`SUBTITLE`, or the Load case names block) join onto the table.
- [ ] 8. **Multi-subcase example.** A small Nastran-like `.h5` with CBUSH forces for 3–4
      subcases (made by `tools/make_synthetic_h5.py`), plus a test that runs the HDF5 block
      → CBUSH margins script → envelope/requirement check over all subcases.
- [ ] 9. **Close the gaps found in step 8** (GUI subcase multi-select, preview, margin
      script, report table) and add an example pipeline `examples/multi_loadcase.apb.json`.
- [ ] 10. Release 0.2.0: changelog entry, tag, push.
