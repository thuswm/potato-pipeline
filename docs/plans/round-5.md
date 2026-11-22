# PLAN — round 5: GitHub introduction (2026-11-22)

Status: **complete 2026-11-22**. Part A released as v0.1.0–v0.1.2; part B moved out of the round (see below). Completed plans are archived in `docs/plans/`.

Source: the "Next Step" section of the user's notes after round 4. Each step: spec → user
review → code → tests (`ruff format`, `ruff check`, `mypy src`, `pytest`) → user asks for the
commit. Tick the boxes as work is done.

## Decisions

| Topic | Decision |
|-------|----------|
| Name | Display name **Potato Pipeline**. Package `apb`, command `apb` and `*.apb.json` files are unchanged |
| Repository | Public, `github.com/thuswm/potato-pipeline` |
| License | None for now; the user decides later (MIT was suggested). Without a license file the code is visible but "all rights reserved" |
| Several load cases | Moved out of this round (2026-11-22): the function already exists; what is left is decided after the user's Windows test |

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
- [x] 5. **Prepare the repository.**
      - Check what becomes public: `CLAUDE.md`, `PLAN.md` and `docs/plans/` stay as project
        history. `my_notes.md` is already git-ignored. The example FEM data is fake data. No
        local paths or personal data in tracked files (already checked: none found).
      - pyproject: `[project.urls]` (Homepage, Issues), `authors`.
      - README: short and fit for GitHub (what it is, screenshot, install with uv, install
        without uv, quick start, development, link to the specs).
      - No license file in this round (decision 2026-11-22). Adding one later is a separate step.
- [x] 6. **Push.** `gh repo create thuswm/potato-pipeline --public`, push `main` and tag
      `v0.1.0`. Check that CI (Ubuntu and Windows, Python 3.12 and 3.13) passes on GitHub and
      fix any failures.
      Done: v0.1.0 pushed; Windows CI failed (type check, path-separator tests, one display
      bug), fixed on branch `ci/windows-fix` and released as v0.1.1. Two flaky browser tests
      fixed and CI limited to branch pushes in v0.1.2 (pushed without CI at the user's request).

## Part B — Several load cases in one table (moved out of the round)

Need: a Nastran analysis has several load cases (subcases), and each one outputs e.g.
CBUSH forces. The analyst checks all load cases, so they must all be in **one** table.

**Finding (2026-11-22):** this already works. The HDF5 result block reads every selected
subcase into one long table with a `SUBCASE` column (empty selection = all subcases). The
`fastener_loads` example does it for CBUSH forces of four subcases on synthetic data (load case
names, load case table, governing margin), and tests cover it. It has not been shown on a
**real** Nastran file with several subcases, because the reference files have one subcase each.

What would remain is verification and convenience, written up as a proposal in spec 02 §3.10:
load case names from the file's case control, names in the subcase list, a real multi-subcase
Nastran run (`global_loads.bdf` with several `SUBCASE`s, run by the user) and an example
pipeline of the CBUSH margin chain. Decision: the round closes here; the next plan is written
from the user's test on the Windows work computer and takes over what still matters.

- [x] 7. Spec 02 §3.10 written and committed as a proposal (not scheduled).
- ~~8.–10.~~ Not done in this round (see above).
