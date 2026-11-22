# 12 — Versioning and Releases

Status: **approved** (2026-11-22, round 5 step 3)

## 1. Purpose

The tool goes to GitHub so that it can be installed and tested on other computers. Every
installed copy must say which version it is. Every version must say what changed and
whether old pipeline files still open. A tester must be able to install exactly the
version they were asked to test.

## 2. Requirements

| ID | Requirement | Prio |
|----|-------------|------|
| VER-01 | Version numbers follow [Semantic Versioning](https://semver.org): `MAJOR.MINOR.PATCH` | M |
| VER-02 | The version is kept in **one** place: `version` in `pyproject.toml` | M |
| VER-03 | The program reports its version: `apb --version`, `apb version`, the GUI toolbar, and the "Generated … with Potato Pipeline X.Y.Z" line in reports | M |
| VER-04 | `CHANGELOG.md` at the repository root lists the changes of every release | M |
| VER-05 | Every push to `main` on GitHub is a release: a git tag `vX.Y.Z` that can be installed by that tag | M |
| VER-08 | The plan of each round states the release number it ends with (normally the next minor) | M |
| VER-06 | The changelog marks every change that stops old pipeline files from opening, or that changes results | M |
| VER-07 | A GitHub release page with the changelog entry for every tag | L |

## 3. Design

### 3.1 Version numbers

| Part | Raised when | Before 1.0 |
|------|-------------|------------|
| MAJOR | Old pipeline files no longer open, or blocks change meaning | Stays 0. While it is 0, a MINOR bump may break things |
| MINOR | New blocks or features (normally one plan round = one minor release) | Normally every round |
| PATCH | Bug fixes only | As needed |

**Every push to `main` on GitHub is a release.** Which version a round ends with is decided
when its plan is written (VER-08). Normally it is the next minor, and a plan may split a round
into more than one release (e.g. round 5: 0.1.0 after part A, 0.2.0 after part B).

Between releases the local `main` carries the next version with a `.devN` suffix (PEP 440).
Example: release `0.1.0`, then local work is `0.2.0.dev0` until `0.2.0` is released. Because
only releases are pushed, a `.dev` version never appears on GitHub, but a local copy can
always be told apart from a release.

Version 1.0 is reached once the pipeline file format is considered stable (see 3.4).

### 3.2 One source of truth

- `pyproject.toml` `version = "…"` is the only place where the number is written.
- `apb/__init__.py` reads it from the installed package metadata:
  `__version__ = importlib.metadata.version("analysis-pipeline-builder")`. This replaces the
  hard-coded string that is there today.
- A fallback `"0+unknown"` is used if the package is not installed, e.g. when `src/` is on
  the path without an install. This should not happen with uv or pip, but must not crash.
- No extra tools such as setuptools-scm or bump scripts. A release changes one line by
  hand.

### 3.3 Where the version is shown

| Place | Today | Change |
|-------|-------|--------|
| `apb version` | prints the version | unchanged |
| `apb --version` | missing | new, the common convention (prints the version and exits) |
| GUI toolbar | `vX.Y.Z` on the right | unchanged |
| Word report | "Generated … with Potato Pipeline X.Y.Z" | unchanged |
| Stored outputs, solver run records | `tool_version` saved | unchanged (see 3.5) |

### 3.4 Version of the pipeline file format

The `.apb.json` file already has its own `schema_version` (spec 01, currently `1`). It is
separate from the tool version:

- `schema_version` only changes when the file layout changes. Many releases share one
  schema.
- A release that raises `schema_version` says so in the changelog (VER-06). It either
  reads the old schema and upgrades it on load, or reports clearly that the file is too
  old.
- A file with a newer `schema_version` than the tool knows is refused, with the message
  "made with a newer Potato Pipeline; please update".
  **Change:** today such a file fails with a raw validation error
  (`schema_version: Literal[1]`). This round adds the clear message.

### 3.5 Stored outputs and version changes

Stored outputs (spec 08) are reused only if they were made by the **same** tool version
(`tool_version == __version__`). After an update, every pipeline therefore shows "not
run" until it is run again. This is kept on purpose: a new version may compute
differently, and a report must never mix results from two versions.

### 3.6 Changelog

`CHANGELOG.md` uses the [Keep a Changelog](https://keepachangelog.com) layout:

```markdown
# Changelog

## [Unreleased]

## [0.1.0] - 2026-11-22
### Added
- Graphical pipeline editor (browser), command-line runner …
### Changed / Fixed / Removed
- …
### Breaking
- (pipeline files or results affected; empty for 0.1.0)
```

The 0.1.0 entry is a short summary of rounds 1–4, one line per feature area, taken from
`docs/plans/`. From then on, every plan round ends by moving its `Unreleased` notes into a
new version section.

### 3.7 Release steps (end of a plan round)

1. Set `version` in `pyproject.toml` to the release number (drop `.devN`) and run `uv lock`.
2. Move the `Unreleased` notes in `CHANGELOG.md` to `## [X.Y.Z] - date`.
3. Commit "Release X.Y.Z", then tag it: `git tag -a vX.Y.Z -m "Release X.Y.Z"`.
4. Push `main` and the tag: `git push --follow-tags`. CI must pass.
5. Locally, set `version` to the next `X.(Y+1).0.dev0` and commit (pushed with the next release).

### 3.8 Installing a given version

```sh
pip install git+https://github.com/thuswm/potato-pipeline@v0.1.0   # a release
pip install git+https://github.com/thuswm/potato-pipeline           # latest release (= main)
```

`apb --version` then shows what is installed.

## 4. Out of scope (this round)

- Publishing to PyPI. The distribution name `analysis-pipeline-builder` stays, and is
  revisited if the tool is ever published.
- GitHub release pages (VER-07) and automatic release builds from tags.
- Checking at runtime whether a newer version exists.

## 5. Decisions

1. Every push to GitHub `main` is a release. The release number is decided in the plan,
   normally one minor release per round (user, 2026-11-22).
